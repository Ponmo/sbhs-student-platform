from flask_socketio import SocketIO, emit, join_room, leave_room

import json
import os
import re

from flask import Flask, flash, redirect, Markup, url_for, session, request, jsonify, Response, request, Request
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

#from flask_login import LoginManager
#login_manager = LoginManager()

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
collection_emails = db['Emails']

# Support SSL termination. Mutate the host_url within Flask to use https://
# if the SSL was terminated.

class ProxiedRequest(Request):
    def __init__(self, environ, populate_request=True, shallow=False):
        super(Request, self).__init__(environ, populate_request, shallow)
        x_forwarded_proto = self.headers.get('X-Forwarded-Proto')
        if  x_forwarded_proto == 'https':
            self.url = self.url.replace('http://', 'https://')
            self.host_url = self.host_url.replace('http://', 'https://')
            self.base_url = self.base_url.replace('http://', 'https://')
            self.url_root = self.url_root.replace('http://', 'https://')

app = Flask(__name__)
app.secret_key = os.environ['SECRET_KEY']
app.request_class = ProxiedRequest

socketio = SocketIO(app, async_mode='gevent')

client = WebApplicationClient(GOOGLE_CLIENT_ID)

@app.route('/send_email', methods=['GET', 'POST'])
def send_email():
    if request.method == 'POST':
        sender_email = 'sbhs.platform.test@gmail.com'
        password = os.environ['EMAIL_ACCESS_PASSWORD']
        message = MIMEMultipart('alternative')
        message['Subject'] = request.json['subject']
        message['From'] =  'Platform Test'
        recipients = request.json['to']
        stored_recipients = list(set(recipients))
        if 'Everyone' in recipients:
            everyone = collection_users.find({'joined': session['current_space']})
            for recipient in everyone:
                recipients.append(recipient['email'])
        recipients.append(session['users_email'])
        recipients = list(set(recipients))
        text = (request.json['message'].replace('\n', '<br />') + '<br>' +
        '--------------------------------------<br>' +
        session['users_name'] + '<br>' + 
        session['users_email'] + '<br>' +
        '<a href="https://sbhs-platform.herokuapp.com/sbhs/' + request.json['space_id'] + '">' + request.json['space_name'] + '</a><br>' +
        '--------------------------------------<br>' +
        '<div style="color:lightgray;">do not reply</div>')
        message.attach(MIMEText(text, 'html'))
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=context) as server:
            server.login(sender_email, password)
            server.sendmail(sender_email, recipients, message.as_string())
        collection_emails.insert_one({'name': session['users_name'], 'picture': session['picture'], 'room': request.json['room_id'], 'email': session['users_email'], 'datetime': datetime.now().isoformat() + 'Z', 'from': session['unique_id'], 'recipients': stored_recipients, 'subject': request.json['subject'], 'message': request.json['message']})
        return Response(dumps({'success': 'true'}), mimetype='application/json')
    return Response(dumps({'success': 'false'}), mimetype='application/json')


# Redirects users on http to https.
# Does not work with Heroku deployments

#@app.before_request
#def before_request():
#    if not request.is_secure:
#        url = request.url.replace('http://', 'https://', 1)
#        code = 301
#        return redirect(url, code=code)

# Loads login page.
# If login fails, load page again with error message.

@app.route('/') 
def render_login():
    if 'http://' in request.url:
        return redirect(request.url.replace('http://', 'https://', 1), 301)
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
    session['logged'] = True
    session['current_space'] = ''
    session['current_space_name'] = ''
    
    # Store user data in MongoDB if new user.
    
    if not collection_users.count_documents({ '_id': unique_id}, limit = 1):
        collection_users.insert_one({'_id': unique_id, 'name': users_name, 'email': users_email, 'picture': picture, 'joined': []}) #check if profile picture the same !
    return redirect(url_for('render_main_page'))

# Handle errors to Google API call.

def get_google_provider_cfg():
    return requests.get(GOOGLE_DISCOVERY_URL).json()
    
# Loads platform after login. Comment

@app.route('/sbhs')
@app.route('/sbhs/<space_id>')
def render_main_page(space_id = None):
    if space_id is not None:
        if 'logged' not in session or session['logged'] == False:
            session['invite'] = space_id
            return redirect(url_for('render_login'))
    if 'invite' in session:
        space_id = session['invite']
        session.pop('invite')
        return redirect('https://sbhs-platform.herokuapp.com/sbhs/' + space_id) #TypeError: can only concatenate str (not "NoneType") to str
    if 'logged' not in session or session['logged'] == False:
       return redirect(url_for('render_login'))
    return render_template('index.html', user_name = session['users_name'], room = '1', user_picture = session['picture'], user_id = session['unique_id'])

# When logout button is clicked, destroy session.

@app.route('/logout', methods=['GET', 'POST'])
def logout():
    if request.method == 'POST':
        session['logged'] = False # Prevents browsers from using cached session data to log in.
        session.clear()
        return Response(dumps({'success': 'true'}), mimetype='application/json')
        
# Returns all space data from MongoDB.

@app.route('/list_spaces', methods=['GET', 'POST'])
def list_spaces():
    if request.method == 'POST':
        user_spaces = collection_users.find_one({"_id": session['unique_id']})['joined']
        space_list = []
        for space_item in user_spaces:
            try:
                space_list.append(collection_spaces.find_one({'_id': ObjectId(space_item)}))
            except:
                pass
        all_spaces = list(collection_spaces.find())
        data = [all_spaces, space_list]
        return Response(dumps(data), mimetype='application/json')

# Returns user's joined spaces.

@app.route('/user_spaces', methods=['GET', 'POST'])
def user_spaces():
    if request.method == 'POST':
        user_spaces = collection_users.find_one({"_id": session['unique_id']})['joined']
        space_list = []
        for space_item in user_spaces:
            try:
                space_list.append(collection_spaces.find_one({'_id': ObjectId(space_item)}))
            except:
                pass
        space_list = dumps(space_list)
        return Response(space_list, mimetype='application/json')

# Returns all room and section data of the clicked space from MongoDB.

@app.route('/space', methods=['GET', 'POST'])
def render_space():
    if request.method == 'POST':
        rooms_and_sections = dumps([list(collection_rooms.find({'space': request.json['space_id']}).sort('order', 1)), list(collection_sections.find({'space': request.json['space_id']}).sort('order', 1)), list(collection_users.find({'joined': {'$in': [request.json['space_id']]}})), list(collection_spaces.find({'_id': ObjectId(request.json['space_id'])}))]) #find way to convert find_one 
        session['current_space'] = request.json['space_id']
        session['current_space_name'] = collection_spaces.find_one({'_id': ObjectId(request.json['space_id'])})['name']
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
        chat_history = dumps(list(collection_messages.find({'room': request.json['room_id']}).sort('_id', pymongo.DESCENDING).skip(int(request.json['i'])).limit(25)))
        return Response(chat_history, mimetype='application/json')

@app.route('/email_history', methods=['GET', 'POST']) #return only emails users can see, and check if space admin
def email_history():
    if request.method == 'POST':
        email_history = collection_emails.find({'room': request.json['room_id']}).sort('_id', pymongo.DESCENDING).skip(int(request.json['i'])).limit(25)
        email_list = []
        for email in email_history:
            if session['users_email'] in email['recipients'] or 'Everyone' in email['recipients'] or space_admin():
                email_list.append(email)
        return Response(dumps(email_list), mimetype='application/json')

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

# Deletes a section from MongoDB.
# Updates the order and positioning of the other 
# sections and rooms in the space.

@app.route('/delete_section', methods=['GET', 'POST'])
def delete_section():
    if request.method == 'POST':
        section_count = collection_sections.count_documents({'space': request.json['space_id']})
        if section_count > 1:
            collection_sections.delete_one({'_id': ObjectId(request.json['section_id'])})
            order = 1
            section_list = collection_sections.find({'space': request.json['space_id']}).sort('order', 1)
            for section in section_list:
                collection_sections.update_one({'_id': section['_id']}, {'$set': {'order': order}})
                order += 1
            first_section = str(collection_sections.find_one({'space': request.json['space_id'], 'order': 1})['_id'])
            order = collection_rooms.count_documents({'section': first_section}) + 1
            room_list = collection_rooms.find({'section': request.json['section_id']}).sort('order', 1)
            for room in room_list:
                collection_rooms.update_one({'_id': room['_id']}, {'$set': {'order': order, 'section': first_section}})
                order += 1
            return Response(dumps({'success': 'true'}), mimetype='application/json')
        else:
            return Response(dumps({'success': 'false'}), mimetype='application/json')

# Adds the newly created space, default room, and default
# section to MongoDB.
# Returns the room and section data.

@app.route('/create_space', methods=['GET', 'POST']) #Check if space with name already exists...
def create_space():
    if request.method == 'POST':
        space_id = ObjectId()
        room_id = ObjectId()
        email_room_id = ObjectId()
        section_id = ObjectId()
        room = {'_id': room_id, 'space': str(space_id), 'section': str(section_id), 'name': 'general', 'order': 1}
        special_rooms = {'_id': email_room_id, 'space': str(space_id), 'section': 'special', 'name': 'Email', 'order': 1}
        section = {'_id': section_id, 'space': str(space_id), 'name': 'discussion', 'order': 1}
        collection_spaces.insert_one({'_id': space_id, 'name': request.json['space_name'], 'picture': request.json['space_image'], 'admins': [session['unique_id']], 'members': [[session['unique_id'], session['users_name']]]})
        collection_rooms.insert_many([room, special_rooms])
        collection_sections.insert_one(section)        
        joined = collection_users.find_one({"_id": session['unique_id']})['joined']
        joined.append(str(space_id))
        collection_users.find_one_and_update({"_id": session['unique_id']}, {'$set': {'joined': joined}})
        session['current_space'] = str(space_id)
        session['current_space_name'] = request.json['space_name']
        return Response(dumps({'space_id': str(space_id)}), mimetype='application/json')

# Adds space to user's list of joined spaces in MongoDB.
# Returns space data.
# TODO: Check if space still exists in MongoDB.

#todo for tomorrow
@app.route('/delete_space', methods=['GET', 'POST'])
def delete_space():
    if request.method == 'POST':
        collection_spaces.find_one({'_id': ObjectId(request.json['space_id'])})
        collection_spaces.delete_one({'_id': ObjectId(request.json['space_id'])})
        session['current_space'] = ''
        session['current_space_name'] = ''
        return Response(dumps({'success': 'true'}), mimetype='application/json')
    return Response(dumps({'success': 'false'}), mimetype='application/json')

@app.route('/join_space', methods=['GET', 'POST'])
def join_space():
    if request.method == 'POST':
        joined = collection_users.find_one({"_id": session['unique_id']})['joined']
        space = dumps(collection_spaces.find_one({'_id': ObjectId(request.json['space_id'])}))
        if request.json['space_id'] not in joined:
            joined.append(request.json['space_id'])
            collection_users.find_one_and_update({"_id": session['unique_id']}, {'$set': {'joined': joined}})
            collection_spaces.find_one_and_update({"_id": ObjectId(request.json['space_id'])}, {'$push': {'members': [session['unique_id'], session['users_name']]}})
        session['current_space'] = request.json['space_id']
        session['current_space_name'] = collection_spaces.find_one({'_id': ObjectId(request.json['space_id'])})['name']
        return Response(space, mimetype='application/json')

# When user deletes a message, delete that message from MongoDB.
# If the combine status of the next message is true, then
# change the combine status of it to false.
# TODO: Add deleted message to the report collection in MongoDB.

@app.route('/delete_message', methods=['GET', 'POST'])
def delete_message():
    if request.method == 'POST':
        deleted_message = collection_messages.find_one({'_id': ObjectId(request.json['message_id'])})
        deleted_email = collection_messages.find({'room': request.json['room_id'], 'email': deleted_message['email']}).sort('_id', pymongo.DESCENDING)
        document_list = list(deleted_email)
        message_index = document_list.index(deleted_message)
        if message_index != 0:
            if document_list[message_index-1]["combine"] == "true":
                collection_messages.find_one_and_update({'_id': ObjectId(document_list[message_index-1]['_id'])}, {'$set': {'combine': 'false'}}) 
        #collection_deleted.insert_one({'name': session['users_email'], 'datetime': datetime.now().isoformat() + 'Z', 'deleted_message_content': deleted_message}) Used to add to logs once deleted.
        collection_messages.delete_one({"_id": ObjectId(request.json['message_id'])})
        collection_logs.insert_one({'name': session['users_name'], 'user_id': session['unique_id'], 'email': session['users_email'], 'action': 'deleted message', 'by': deleted_message['name'], 'by_email': deleted_message['email'], 'in': session['current_space_name'], 'details': deleted_message, 'datetime': datetime.now().isoformat() + 'Z'})
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
        if collection_logs.count_documents({'details': reported_message}) == 0:
            collection_logs.insert_one({'name': session['users_name'], 'user_id': session['unique_id'], 'email': session['users_email'], 'action': 'reported message', 'by': reported_message['name'], 'by_email': reported_message['email'], 'in': session['current_space_name'], 'details': reported_message, 'datetime': datetime.now().isoformat() + 'Z'})
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
        queried_spaces = []
        names_list = []
        for spaces in member['joined']:
            try: 
                space = collection_spaces.find_one({'_id': ObjectId(spaces)})
                queried_spaces.append([space['picture'], spaces])
                names_list.append(space['name'])
            except:
                pass
        user_data = {'name': member['name'], 'email': member['email'], 'picture': member['picture'], 'joined': queried_spaces, 'joined_spaces_names': names_list}
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

@app.route('/server_logs', methods=['GET', 'POST'])
def server_logs():
    if request.method == 'POST':
        logs = list(collection_logs.find().sort('_id', pymongo.DESCENDING).skip(int(request.json['i'])).limit(25))
        start = 0
        info = []
        for index, item in enumerate(logs):
            info.append(str(item['_id']))
            info.append(request.json['last_loaded'])
            if str(item['_id']) == request.json['last_loaded']:
                break
            else:
                start = index
        if start != 24:
            del logs[:start - 1]
        return Response(dumps([logs, info]), mimetype='application/json')

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
    data['message_id'] = str(ObjectId())
    data['user_id'] = session['unique_id']
    data['picture'] = session['picture']
    data['name'] = session['users_name']
    latest_message = collection_messages.find_one({'room': data['room_id']}, sort=[( '_id', pymongo.DESCENDING )])
    try:
        duration = datetime.now() - datetime.fromisoformat(latest_message.get('datetime').replace('Z', ''))
        if latest_message.get('name') == session['users_name'] and latest_message.get('picture') == session['picture'] and duration.total_seconds() < 180: #deprecate
            data['combine'] = 'true'
        else:
            data['combine'] = 'false'
    except:
        data['combine'] = 'false'
    collection_messages.insert_one({'_id': ObjectId(data['message_id']), 'name': data['name'], 'user_id': data['user_id'], 'picture': data['picture'], 'room': data['room_id'], 'datetime': utc_dt, 'message': data['message'], 'combine': data['combine'], 'email': session['users_email']})
    socketio.emit('receive_message', data, room = data['room_id'])
    
# When a room is created, send that room data to all
# users in the space.

@socketio.on('created_room')
def created_room(data):
    for room in data['room_list']: #plug list
        socketio.emit('created_room', data, room = room)

# When a room is deleted, send that room data to all
# users in the space.

@socketio.on('deleted_room')
def deleted_room(data):
    for room in data['room_list']:
    	socketio.emit('deleted_room', data, room = room)

#todo for tomorrow
@socketio.on('deleted_space')
def deleted_space(data):
    for room in data['room_list']:
        socketio.emit('deleted_space', data, room = room)

# When a section is created, send that section data to all
# users in the space.

@socketio.on('created_section')
def created_section(data):
	for room in data['room_list']:
		socketio.emit('created_section', data, room = room)

# When a section is deleted, send that section data to all
# users in the space.

@socketio.on('deleted_section')
def created_section(data):
	for room in data['room_list']:
		socketio.emit('deleted_section', data, room = room)

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

@socketio.on('sorted_sections')
def sorted_sections(data):
    for section in data['section_list']:
        collection_sections.find_one_and_update({"_id": ObjectId(section)}, {'$set': {'order': data['section_list'].index(section) + 1}})
    for room in data['room_list']:
        socketio.emit('sorted_sections', data, room = room)
    
# When rooms are sorted, update the order in MongoDB.

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

@socketio.on('sent_email')
def sent_email(data):
    socketio.emit('sent_email', data, room = data['room_id'])

@socketio.on('joined_space')
def joined_space(data):
    user = collection_users.find_one({'_id': session['unique_id']})
    for room in data['room_list']:
        emit('joined_space', user, room = room, include_self=False)

def space_admin():
    if session['unique_id'] in collection_spaces.find_one({'_id': ObjectId(session['current_space'])})['admins']:
        return True
    return False

#if __name__ == '__main__':
#    socketio.run(app, debug=False)