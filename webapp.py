from flask_socketio import SocketIO, emit, join_room, leave_room

import json
import os
import re

from flask import Flask, redirect, Markup, url_for, session, request, jsonify, Response, request
from flask import render_template

from oauthlib.oauth2 import WebApplicationClient
import requests

from bson.objectid import ObjectId
from bson.json_util import dumps

#import pprint
#import sys
import pymongo
from datetime import datetime, date, timedelta
import pytz
from pytz import timezone

import smtplib, ssl
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

GOOGLE_CLIENT_ID = os.environ['GOOGLE_CLIENT_ID']

GOOGLE_CLIENT_SECRET = os.environ['GOOGLE_CLIENT_SECRET']
GOOGLE_DISCOVERY_URL = (
    'https://accounts.google.com/.well-known/openid-configuration'
)

connection_string = os.environ['MONGO_CONNECTION_STRING']
db_name = os.environ['MONGO_DBNAME']
client = pymongo.MongoClient(connection_string)
db = client[db_name]
collection_users = db['Users']
collection_spaces = db['Spaces']
collection_rooms = db['Rooms']
collection_messages = db['Messages']
collection_sections = db['Sections']
collection_reports = db['Reports']
collection_deleted = db['Deleted Messages']
collection_logs = db['Logs']

app = Flask(__name__)
app.secret_key = os.environ['SECRET_KEY']

socketio = SocketIO(app, async_mode='gevent')

client = WebApplicationClient(GOOGLE_CLIENT_ID)


#@app.route('/send_email')
#def send_email():
#    try:
#        #get email and password of email bot through heroku environment.
#        smtp_server = 'smtp.gmail.com'
#        sender_email = #bot email
#        password = #bot password
#        message = MIMEMultipart('alternative')
#        message['Subject'] = 'SBHS Parent Board Notification' #subject of automatic email
#        message['From'] = sender_email #email of bot
#        message['To'] = receiver_email #sends to this email
#        text = """\
#        """ #basic text
#        html = """\
#        """ #text version with html
#        part1 = MIMEText(text, 'plain')
#        part2 = MIMEText(html, 'html')
#        message.attach(part1)
#        message.attach(part2)
#        context = ssl.create_default_context()
#        with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=context) as server:
#            server.login(sender_email, password) #logs into the bot email
#            #server.sendmail(sender_email, receiver_email, message.as_string()) #sends email
#    except:
#        return
#    return


# Redirects users on http to https.

@app.before_request
def before_request():
    if not request.is_secure:
        url = request.url.replace('http://', 'https://', 1)
        code = 301
        return redirect(url, code=code)

# Loads login page.
# If login fails, load page again with error message.

@app.route('/') 
def render_login():
    if request.args.get('error') != None:
        return render_template('login.html', login_error = request.args.get('error'))
    return render_template('login.html')

@app.route('/login')
def login():
    
    # Finds URL for Google login.
    
    google_provider_cfg = get_google_provider_cfg()
    authorization_endpoint = google_provider_cfg['authorization_endpoint']

    # Use library to construct the request for Google login and provide
    # scopes that let you retrieve user's profile from Google
    
    request_uri = client.prepare_request_uri(
        authorization_endpoint,
        redirect_uri=request.base_url + '/callback',
        scope=['openid', 'email', 'profile'],
        prompt='consent'
    )
    return redirect(request_uri)

@app.route('/login/callback')
def callback():
    
    # Get authorization code from Google
    
    code = request.args.get('code')
    google_provider_cfg = get_google_provider_cfg()
    token_endpoint = google_provider_cfg['token_endpoint']
    token_url, headers, body = client.prepare_token_request(
        token_endpoint,
        authorization_response=request.url,
        redirect_url=request.base_url,
        code=code
    )
    token_response = requests.post(
        token_url,
        headers=headers,
        data=body,
        auth=(GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET),
    )

    # Parse tokens
    
    client.parse_request_body_response(json.dumps(token_response.json()))

    # Finds URL from Google that contains user's profile information.
    
    userinfo_endpoint = google_provider_cfg['userinfo_endpoint']
    uri, headers, body = client.add_token(userinfo_endpoint)
    userinfo_response = requests.get(uri, headers=headers, data=body)

    # Check if user's email is verified by Google, after user has authenticated with Google and has authorized this app.
    # If verified, get user data and check if their email in school domains.
    # If not verified or email not in school domains, return user to login page with error.
    
    if userinfo_response.json().get('email_verified'):
        unique_id = userinfo_response.json()['sub']
        users_email = userinfo_response.json()['email']
        picture = userinfo_response.json()['picture']
        users_name = userinfo_response.json()['name']
        #if not users_email.endswith('@my.sbunified.org') and not users_email.endswith('@sbunified.org'):
            #return redirect(url_for('render_login', error = "Please use your SBUnified school email"))
    else:
        return redirect(url_for('render_login', error = "Email not available or verified"))
    
    # Store user data in session
    
    session['unique_id'] = unique_id
    session['users_email'] = users_email
    session['picture'] = picture
    session['users_name'] = users_name
    
    # Store user data in MongoDB if new user.
    
    if not collection_users.count_documents({ '_id': unique_id}, limit = 1):
        collection_users.insert_one({'_id': unique_id, 'name': users_name, 'email': users_email, 'picture': picture, 'joined': []}) #check if profile picture the same !
    return redirect(url_for('render_main_page'))

# Handle errors to Google API call.

def get_google_provider_cfg():
    return requests.get(GOOGLE_DISCOVERY_URL).json()
    
# Loads platform after login.

@app.route('/sbhs')
def render_main_page():
    if 'users_name' in session:
        return render_template('index.html', name = session['users_name'], room = '1', picture = session['picture'], user_id = session['unique_id'])
    else:
        return redirect(url_for('render_login'))

# When logout button is clicked, clear session.

@app.route('/logout')
def logout():
    session.clear()

# Returns all space data from MongoDB.

@app.route('/list_spaces', methods=['GET', 'POST'])
def list_spaces():
    if request.method == 'POST':
        spaces_list = list(collection_spaces.find())
        user_id = session['unique_id']
        data = [spaces_list, user_id]
        return Response(dumps(data), mimetype='application/json')

# Returns all room and section data of the clicked space from MongoDB.

@app.route('/space', methods=['GET', 'POST'])
def render_space():
    if request.method == 'POST':
        results = {'processed': request.json['space_id']}
        rooms_and_sections = dumps([list(collection_rooms.find({'space': request.json['space_id']}).sort('order', 1)), list(collection_sections.find({'space': request.json['space_id']}).sort('order', 1)), list(collection_users.find({'joined': {'$in': [request.json['space_id']]}}))])
        return Response(rooms_and_sections, mimetype='application/json')

# When user clicks leave space button, that space is removed
# from their list of joined spaces in MongoDB.
# Returns updated list of joined spaces.

@app.route('/leave_space', methods=['GET', 'POST'])
def leave_space():
    if request.method == 'POST':
        joined = collection_users.find_one({"_id": session['unique_id']})['joined']
        joined.remove(request.json['space-id'])
        collection_users.update_one({"_id": session['unique_id']}, {"$set": {"joined": joined}})
        collection_spaces.update_one({"_id": ObjectId(request.json['space-id'])}, { "$pull": {"members": [session['unique_id'], session['users_name']]}})
        joined = dumps(joined)
        return Response(joined, mimetype='application/json')

# Returns selected range of messages of the loaded room.
# Called either when user first loads a room
# or when more messages need to be loaded as user scrolls.

@app.route('/chat_history', methods=['GET', 'POST'])
def chat_history():
    if request.method == 'POST':
        chat_history = dumps(list(collection_messages.find({'room': request.json['room_id']}).sort('_id', pymongo.DESCENDING).skip(int(request.json['i'])).limit(100)))
        return Response(chat_history, mimetype='application/json')

# Deletes the room and all of its messages in MongoDB.

@app.route('/delete_room', methods=['GET', 'POST'])
def delete_room():
	if request.method == 'POST':
		room_count = collection_rooms.count_documents({'space': request.json['space_id']})
		if room_count > 1:
			collection_rooms.delete_one({'_id': ObjectId(request.json['room_id'])})
			collection_messages.delete_many({'room': request.json['room_id']})
			cursor = collection_rooms.find({'section': request.json['section_id']}).sort('order', 1)
			order = 1
			for document in cursor:
				collection_rooms.update_one({'_id': document['_id']}, {'$set': {'order': order}})
				order += 1
			return Response(dumps({'success': 'true'}), mimetype='application/json')
		else:
			return Response(dumps({'success': 'false'}), mimetype='application/json')

# Adds the newly created room to MongoDB.
# Returns the room data.

@app.route('/create_room', methods=['GET', 'POST'])
def create_room():
	if request.method == 'POST':
		room_id = ObjectId()
		room_list = list(collection_rooms.find({'space': request.json['space_id'], 'section': request.json['section_id']}))
		room = {'_id': room_id, 'space': request.json['space_id'], 'section': request.json['section_id'], 'name': request.json['room_name'], 'order': len(room_list) + 1}
		collection_rooms.insert_one(room)
		room = dumps(room)
		return Response(room, mimetype='application/json')

# Adds the newly created section to MongoDB.
# Returns the section data.

@app.route('/create_section', methods=['GET', 'POST'])
def create_section():
	if request.method == 'POST':
		section_id = ObjectId()
		section_list = list(collection_sections.find({'space': request.json['space_id']}))
		section = {'_id': section_id, 'space': request.json['space_id'], 'name': request.json['section_name'], 'order': len(section_list) + 1}
		collection_sections.insert_one(section)
		section = dumps(section)
		return Response(section, mimetype='application/json')

# Adds the newly created space, default room, and default
# section to MongoDB.
# Returns the room and section data.

@app.route('/create_space', methods=['GET', 'POST'])
def create_space():
    if request.method == 'POST':
        space_id = ObjectId()
        room_id = ObjectId()
        section_id = ObjectId()
        room = {'_id': room_id, 'space': str(space_id), 'section': str(section_id), 'name': 'general', 'order': 1}
        section = {'_id': section_id, 'space': str(space_id), 'name': 'discussion', 'order': 1}
        collection_spaces.insert_one({'_id': space_id, 'name': request.json['space_name'], 'picture': request.json['space_image'], 'admins': [session['unique_id']], 'members': [[session['unique_id'], session['users_name']]]})
        collection_rooms.insert_one(room)
        collection_sections.insert_one(section)
        room_and_section = dumps([[room],[section]])
        
        joined = collection_users.find_one({"_id": session['unique_id']})['joined']
        joined.append(str(space_id))
        collection_users.find_one_and_update({"_id": session['unique_id']}, {'$set': {'joined': joined}})
        
        return Response(room_and_section, mimetype='application/json')

# Adds space to user's list of joined spaces in MongoDB.
# Returns space data.
# TODO: Check if space still exists in MongoDB.

@app.route('/join_space', methods=['GET', 'POST'])
def join_space():
    if request.method == 'POST':
        joined = collection_users.find_one({"_id": session['unique_id']})['joined']
        if request.json['space_id'] not in joined:
            joined.append(request.json['space_id'])
            collection_users.find_one_and_update({"_id": session['unique_id']}, {'$set': {'joined': joined}})
            collection_spaces.find_one_and_update({"_id": ObjectId(request.json['space_id'])}, {'$push': {'members': [session['unique_id'], session['users_name']]}})
        space = dumps(collection_spaces.find_one({'_id': ObjectId(request.json['space_id'])}))
        return Response(space, mimetype='application/json')

# Returns user's joined spaces.

@app.route('/user_spaces', methods=['GET', 'POST'])
def user_spaces():
    if request.method == 'POST':
        spaces = collection_users.find_one({"_id": session['unique_id']})['joined']
        space_list = []
        for space_item in spaces:
            try:
                space_list.append(collection_spaces.find_one({'_id': ObjectId(space_item)}))
            except:
                pass
        space_list = dumps(space_list)
        return Response(space_list, mimetype='application/json')

# When user deletes a message, delete that message from MongoDB.
# If the combine status of the next message is true, then
# change the combine status of it to false.
# TODO: Add deleted message to the report collection in MongoDB.

@app.route('/delete_message', methods=['GET', 'POST'])
def delete_message():
    if request.method == 'POST':
        deleted_message = collection_messages.find_one({'_id': ObjectId(request.json['message_id'])})
        deleted_email = collection_messages.find({'email': deleted_message['email']}).sort('_id', pymongo.DESCENDING)
        document_list = list(deleted_email)
        message_index = document_list.index(deleted_message)
        if message_index != 0:
            if document_list[message_index-1]["combine"] == "true":
                collection_messages.find_one_and_update({'_id': document_list[message_index-1]['_id']}, {'$set': {'combine': 'false'}}) 
        #collection_deleted.insert_one({'name': session['users_email'], 'datetime': datetime.now().isoformat() + 'Z', 'deleted_message_content': deleted_message}) Used to add to logs once deleted.
        collection_messages.delete_one({"_id": ObjectId(request.json['message_id'])})
        return Response(dumps({'success': message_index}), mimetype='application/json')
    else:
        return Response(dumps({'success': 'false'}), mimetype='application/json')

# When a user reports a message, add a report with
# relevant information to MongoDB.
# TODO: Store more information like reported time, reason for report, etc.

@app.route('/report_message', methods=['GET', 'POST'])
def report_message():
    if request.method == 'POST':
        reported_message = collection_messages.find_one({'_id': ObjectId(request.json['message_id'])})
        if collection_reports.count_documents({'reported_message_id': reported_message}) == 0:
            collection_reports.insert_one({'reported_message_id': reported_message, 'reported_email': reported_message['email'], 'reported_content': reported_message['message'], 'reporter': session['users_email'], 'datetime': datetime.now().isoformat() + 'Z'})
            return Response(dumps({'success': 'true'}), mimetype='application/json')
        else:
            return Response(dumps({'success': 'many'}), mimetype='application/json')
    else:
        return Response(dumps({'success': 'false'}), mimetype='application/json')
    
# When user accesses another user's profile,
# return their public profile data.

@app.route('/open_member_profile', methods=['GET', 'POST'])
def member_profile():
    if request.method == 'POST':
        member = collection_users.find_one({'_id': request.json['user_id']})
        user_data = {'name': member['name'], 'email': member['email'], 'picture': member['picture'], "joined": member['joined']}
        return Response(dumps(user_data), mimetype='application/json')
    else:
        return Response(dumps({'success': 'false'}), mimetype='application/json')

# When user accesses their own profile,
# return all of their personal profile data.

@app.route('/profile', methods=['GET', 'POST'])
def profile():
    if request.method == 'POST':
        data = collection_users.find_one({'_id': session['unique_id']})
        return Response(dumps(data), mimetype='application/json')
    else:
        return Response(dumps({'success': 'false'}), mimetype='application/json')

@app.route('/sorted_spaces', methods=['GET', 'POST'])
def sorted_spaces():
    if request.method == 'POST':
        collection_users.find_one_and_update({"_id": session['unique_id']}, {'$set': {'joined': request.json['space_list']}})
        return Response(dumps({'success': 'true'}), mimetype='application/json')
    else:
        return Response(dumps({'success': 'false'}), mimetype='application/json')
# When a room is clicked, make user join room
# and leave old room.

@socketio.on('join_room')
def change_room(data):
    try: 
        leave_room(data['old_room'])
    except:
        pass
    join_room(data['room_id'])

# When user starts typing, notify all users in that room.

@socketio.on('is_typing')
def is_typing(data):
	socketio.emit('is_typing', data, room = data['room_id'])

# When user stops typing, notify all users in that room.

@socketio.on('stopped_typing')
def stopped_typing(data):
	socketio.emit('stopped_typing', data, room = data['room_id'])

# When a message is sent, verify and store it in MongoDB.
# Send the message data to all users in that room.

@socketio.on('send_message')
def send_message(data):
    utc_dt = datetime.now().isoformat() + 'Z'
    data['datetime'] = utc_dt
    data['message'] = re.sub('\\\n\\n\\\n+', '\\n\\n', data['message'])
    latest_message = collection_messages.find_one({'room': data['room_id']}, sort=[( '_id', pymongo.DESCENDING )])
    try:
        duration = datetime.now() - datetime.fromisoformat(latest_message.get('datetime').replace('Z', ''))
        if latest_message.get('name') == session['users_name'] and latest_message.get('picture') == session['picture'] and duration.total_seconds() < 180:
            data['combine'] = 'true'
        else:
            data['combine'] = 'false'
    except:
        data['combine'] = 'false'
    data['message_id'] = str(ObjectId())
    collection_messages.insert_one({'_id': ObjectId(data['message_id']),'name': data['name'], 'picture': session['picture'], 'room': data['room_id'], 'datetime': utc_dt, 'message': data['message'], 'combine': data['combine'], 'email': session['users_email']})
    socketio.emit('recieve_message', data, room = data['room_id'])
    
# When a room is created, send that room data to all
# users in the space.

@socketio.on('created_room')
def created_room(data):
    socketio.emit('created_room', data, room = '2948uihe9349')
    
    for room in data['room_list']: #plug list
    	socketio.emit('created_room', data, room = room)

# When a room is deleted, send that room data to all
# users in the space.

@socketio.on('deleted_room')
def deleted_room(data):
    for room in data['room_list']:
    	socketio.emit('deleted_room', data, room = room)

# When a section is created, send that section data to all
# users in the space.

@socketio.on('created_section')
def created_section(data):
	for room in data['room_list']:
		socketio.emit('created_section', data, room = room)

# When a message is deleted, send that message data to all
# users in the space.

@socketio.on('deleted_message')
def deleted_message(data):
    socketio.emit('deleted_message', data, room = data['room_id'])

# When a message is edited, update the message in MongoDB and
# send the message data to all users in that room.

@socketio.on('edited_message')
def edited_message(data):
    collection_messages.find_one_and_update({"_id": ObjectId(data['message_id'])}, {'$set': {'message': data['edit']}})
    socketio.emit('edited_message', data, room = data['room_id'])

# When sections are sorted, update the order in MongoDB.

@socketio.on('sorted_channels')
def sorted_channels(data):
    for section in data['section_list']:
        collection_sections.find_one_and_update({"_id": ObjectId(section)}, {'$set': {'order': data['section_list'].index(section) + 1}})
    for room in data['room_list']:
        socketio.emit('sorted_channels', data, room = room)
    
@socketio.on('sorted_rooms')
def sorted_rooms(data):
    order = 1
    for section in data['room_group_list']:
        if len(section) > 1:
            for room in section[1:]:
                collection_rooms.find_one_and_update({"_id": ObjectId(room)}, {'$set': {'order': order, 'section': section[0]}})
                order += 1
                socketio.emit('sorted_rooms', data, room = room)
        order = 1

if __name__ == '__main__':
    socketio.run(app, debug=False)