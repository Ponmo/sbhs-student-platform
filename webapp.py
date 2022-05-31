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

app = Flask(__name__)
app.secret_key = os.environ['SECRET_KEY']

socketio = SocketIO(app, async_mode='gevent')

client = WebApplicationClient(GOOGLE_CLIENT_ID)

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
        if not users_email.endswith('@my.sbunified.org') and not users_email.endswith('@sbunified.org'):
            return redirect(url_for('render_login', error = "Please use your SBUnified school email"))
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

def get_google_provider_cfg():
    
    # Handle errors to Google API call.
    
    return requests.get(GOOGLE_DISCOVERY_URL).json()
    
# TODO: not currently in use / functional needs logout button

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('render_login'))

# When a room is clicked, make user join room
# and leave old room.

@socketio.on('join_room')
def change_room(data):
    try: 
        leave_room(data['old_room'])
    except:
        pass
    join_room(data['room'])

# When user starts typing, notify all users in that room.

@socketio.on('is_typing')
def is_typing(data):
	socketio.emit('is_typing', data, room = data['room'])

# When user stops typing, notify all users in that room.

@socketio.on('stopped_typing')
def stopped_typing(data):
	socketio.emit('stopped_typing', data, room = data['room'])

# When a message is sent, verify and store it in MongoDB.
# Send the message data to all users in that room.

@socketio.on('send_message')
def send_message(data):
    utc_dt = datetime.now().isoformat() + 'Z'
    data['datetime'] = utc_dt
    data['message'] = re.sub('\\\n\\n\\\n+', '\\n\\n', data['message'])
    latest_message = collection_messages.find_one({'room': data['room']}, sort=[( '_id', pymongo.DESCENDING )])
    try:
        duration = datetime.now() - datetime.fromisoformat(latest_message.get('datetime').replace('Z', ''))
        if latest_message.get('name') == session['users_name'] and latest_message.get('picture') == session['picture'] and duration.total_seconds() < 180:
            data['combine'] = 'true'
        else:
            data['combine'] = 'false'
    except:
        data['combine'] = 'false'
    collection_messages.insert_one({'name': data['name'], 'picture': session['picture'], 'room': data['room'], 'datetime': utc_dt, 'message': data['message'], 'combine': data['combine']})
    socketio.emit('recieve_message', data, room = data['room'])
    
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

# Loads platform after login.

@app.route('/sbhs')
def render_main_page():
    if 'users_name' in session:
        return render_template('index.html', name = session['users_name'], room = '1', picture = session['picture']) # make room gone..
    else:
        return redirect(url_for('render_login'))

# Returns all space data from MongoDB.

@app.route('/list_spaces', methods=['GET', 'POST'])
def list_spaces():
	if request.method == 'POST':
		spaces_list = dumps(list(collection_spaces.find()))
		return Response(spaces_list, mimetype='application/json')

# Returns all room and section data of the clicked space from MongoDB.

@app.route('/space', methods=['GET', 'POST'])
def render_space():
    if request.method == 'POST':
        results = {'processed': request.json['space_id']}
        rooms_and_sections = dumps([list(collection_rooms.find({'space': request.json['space_id']}).sort('order', 1)), list(collection_sections.find({'space': request.json['space_id']}).sort('order', 1))])
        return Response(rooms_and_sections, mimetype='application/json')

@app.route('/leave_space', methods=['GET', 'POST'])
def leave_space():
    if request.method == 'POST':
        joined = collection_users.find_one({"_id": session['unique_id']})['joined'] 
        joined.remove(request.json['space-id'])
        collection_users.update_one({"_id": session['unique_id']}, {"$set": {"joined": joined}})
        return Response(joined, mimetype='application/json')
# https://www.w3schools.com/python/python_mongodb_update.asp
#mycol.update_one({ "address": "Valley 345" }, { "$set": { "address": "Canyon 123" } })

# Returns some message data of the loaded room.
# Called when user first loads a room
# or when more messages need to be loaded as user scrolls.

@app.route('/chat_history', methods=['GET', 'POST'])
def chat_history():
    if request.method == 'POST':
        chat_history = dumps(list(collection_messages.find({'room': request.json['room']}).sort('_id', pymongo.DESCENDING).skip(int(request.json['i'])).limit(100)))
        return Response(chat_history, mimetype='application/json')

# Deletes the room and all of its messages
# in MongoDB.

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
        collection_spaces.insert_one({'_id': space_id, 'name': request.json['space_name'], 'picture': request.json['space_image']})
        collection_rooms.insert_one(room)
        collection_sections.insert_one(section)
        room_and_section = dumps([[room],[section]])
        
        joined = collection_users.find_one({"_id": session['unique_id']})['joined']
        joined.append(str(space_id))
        collection_users.find_one_and_update({"_id": session['unique_id']}, {'$set': {'joined': joined}})
        
        return Response(room_and_section, mimetype='application/json')
	
@app.route('/board', methods=['GET', 'POST'])
def board():
    if request.method == 'POST':
        spaces = dumps(list(collection_spaces.find({})))
        return Response(spaces, mimetype='application/json')

@app.route('/join_space', methods=['GET', 'POST'])
def join_space():
    if request.method == 'POST':
        joined = collection_users.find_one({"_id": session['unique_id']})['joined']
        if request.json['space_id'] not in joined:
            joined.append(request.json['space_id'])
            collection_users.find_one_and_update({"_id": session['unique_id']}, {'$set': {'joined': joined}})
        #implement check if space has been deleted so it cannot find_one try except blocks
        space = dumps(collection_spaces.find_one({'_id': ObjectId(request.json['space_id'])}))
        return Response(space, mimetype='application/json')

@app.route('/user_spaces', methods=['GET', 'POST'])
def user_spaces():
    if request.method == 'POST':
        spaces = collection_users.find_one({"_id": session['unique_id']})['joined']
        space_list = []
        for space_item in spaces:
            try:
                space_list.append(collection_spaces.find_one({'_id': ObjectId(space_item)}))
            except:
                pass#ignore
        space_list = dumps(space_list)
        return Response(space_list, mimetype='application/json')

    #
    # i think it iwortkewd worked! line 187 in html file
    #okay now this python function is done we can go back to javascript to complete the ajaxok

if __name__ == '__main__':
    socketio.run(app, debug=False)