from flask_socketio import SocketIO, emit, join_room, leave_room

import json
import os
import re
import html

from flask import Flask, flash, redirect, Markup, url_for, session, request, jsonify, Response, request, Request
from flask_session import Session
from flask import render_template

from oauthlib.oauth2 import WebApplicationClient
import requests
from bson.objectid import ObjectId
from bson.json_util import dumps
import shortuuid

#import pprint
#import sys
import pymongo
from datetime import datetime, date, timedelta
import pytz
from pytz import timezone

from better_profanity import profanity


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
collection_logs = db['Logs']
collection_emails = db['Emails']
collection_invites = db['Invites']
collection_spaceRequests = db['SpaceRequests']

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
app.config['SESSION_TYPE'] = 'filesystem'
app.request_class = ProxiedRequest
Session(app)

socketio = SocketIO(app, async_mode='gevent', manage_session = False)

client = WebApplicationClient(GOOGLE_CLIENT_ID)

# By default, server-side flask-sessions are permanent

@app.before_first_request
def set_session_lifetime():
    app.permanent_session_lifetime = timedelta(days=1)

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
    if 'https://oneconnected.herokuapp.com' in request.url:
        return redirect('https://www.oneconnected.app')
    if not session_expired():
        return redirect(url_for('render_main_page'))
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
    
    if not collection_users.count_documents({ '_id': unique_id}, limit = 1):
        collection_users.insert_one({'_id': unique_id, 'name': users_name, 'email': users_email, 'picture': picture, 'joined': [], 'status': 'user', 'owns': 0, 'agreed': 'false'}) #check if profile picture the same !
        session['admin'] = False
    else:
        user_status = collection_users.find_one({ '_id': unique_id})['status']
        if user_status == 'banned':
            session['logged'] = False
            session.clear()
            return redirect(url_for('render_login', error = "This account has been banned"))
        elif user_status == 'admin' or user_status == 'owner':
            session['admin'] = True
        else:
            session['admin'] = False

    session['unique_id'] = unique_id
    session['users_email'] = users_email
    session['picture'] = picture
    session['users_name'] = users_name
    session['logged'] = True
    session['current_space'] = ''
    session['current_space_name'] = ''
    #maybe add session['spaces'] = ''
    
    return redirect(url_for('render_main_page'))

# Handle errors to Google API call.

def get_google_provider_cfg():
    return requests.get(GOOGLE_DISCOVERY_URL).json()
    
# Loads platform after login. Comment

@app.route('/school')
@app.route('/school/<space_id>')
def render_main_page(space_id = None):
    if space_id != None:
        if 'logged' not in session or session['logged'] == False:
            session['invite'] = space_id
            return redirect(url_for('render_login'))
    if 'invite' in session:
        space_id = session['invite']
        session.pop('invite')
        if len(space_id) == 7:
            invite = collection_invites.find_one({'_id': space_id})
            if invite == None:
                return render_template('index.html', user_name = session['users_name'], room = '1', user_picture = session['picture'], user_id = session['unique_id'])
            space_id = invite['space']
            session['code'] = space_id
            return redirect('https://www.oneconnected.app/school/' + space_id)
        return redirect('https://www.oneconnected.app/school/' + space_id)
    if 'logged' not in session or session['logged'] == False:
       return redirect(url_for('render_login'))
    return render_template('index.html', user_name = session['users_name'], room = '1', user_picture = session['picture'], user_id = session['unique_id'])

@app.route('/invite/<invite_code>')
def render(invite_code = None):
    if invite_code != None:
        if 'logged' not in session:
            session['invite'] = invite_code
            return redirect(url_for('render_login'))
        space_id = collection_invites.find_one({'_id': invite_code})['space']
        session['code'] = space_id
        return redirect('https://www.oneconnected.app/school/' + space_id)

# When logout button is clicked, destroy session.

@app.route('/logout', methods=['GET', 'POST'])
def logout():
    if session_expired() or banned():
        return 'expired', 200
    if request.method == 'POST':
        session['logged'] = False # Prevents browsers from using cached session data to log in. NOTE: We use server-side sessions now
        session.clear()
        return Response(dumps({'success': 'true'}), mimetype='application/json')

@app.route('/accept_policies', methods=['GET', 'POST'])
def accept_policies():
    if session_expired() or banned():
        return 'expired', 200
    if request.method == 'POST':
        collection_users.update_one({'_id': session['unique_id']}, {'$set': {'agreed': 'true'}}) #used for modal policies
        return Response(dumps({'success':'true'}), mimetype='application/json')
    session['logged'] = False # Prevents browsers from using cached session data to log in. NOTE: We use server-side sessions now
    session.clear()
    return 'not allowed', 405

@app.route('/display_policies', methods=['GET', 'POST'])
def display_policies():
    if session_expired() or banned():
        return 'expired', 200
    if request.method == 'POST':
        profile = collection_users.find_one({'_id': session['unique_id']})
        data = profile['agreed']
        return Response(dumps(data), mimetype='application/json')
    session['logged'] = False # Prevents browsers from using cached session data to log in. NOTE: We use server-side sessions now
    session.clear()
    return 'not allowed', 405

        
# Returns all space data from MongoDB.

@app.route('/list_spaces', methods=['GET', 'POST'])
def list_spaces():
    if session_expired() or banned():
        return 'expired', 200
    if request.method == 'POST':
        user_spaces = collection_users.find_one({"_id": session['unique_id']})['joined']
        space_list = []
        for space_item in user_spaces:
            try:
                space_list.append(collection_spaces.find_one({'_id': ObjectId(space_item)}))
            except:
                pass
        all_spaces = list(collection_spaces.find())
        data = [all_spaces, space_list, str(session['admin'])]
        return Response(dumps(data), mimetype='application/json')
    session['logged'] = False
    session.clear()
    return 'not allowed', 405

# Returns user's joined spaces.

@app.route('/user_spaces', methods=['GET', 'POST'])
def user_spaces():
    if session_expired() or banned():
        return 'expired', 200
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
    session['logged'] = False
    session.clear()
    return 'not allowed', 405

# Returns all room and section data of the clicked space from MongoDB.

@app.route('/space', methods=['GET', 'POST'])
def render_space():
    if session_expired() or banned():
        return 'expired', 200
    if request.method == 'POST': # quicken query by querying ('$in') members with array of ObjectIds from members array...
        space = collection_spaces.find_one({'_id': ObjectId(request.json['space_id'])})
        members = collection_users.find({'_id': {'$in': [i[0] for i in space['members']] + space['banned']}})
        rooms_and_sections = dumps([list(collection_rooms.find({'space': request.json['space_id']}).sort('order', 1)), list(collection_sections.find({'space': request.json['space_id']}).sort('order', 1)), list(members), list(collection_spaces.find({'_id': ObjectId(request.json['space_id'])}).limit(1)), list(collection_invites.find({'space': request.json['space_id']}))])
        if session['unique_id'] in space['admins'] or server_admin():
            session['current_space'] = request.json['space_id']
            session['current_space_name'] = space['name']
            return Response(rooms_and_sections, mimetype='application/json')
        elif any(session['unique_id'] in item for item in space['members']):
            session['current_space'] = request.json['space_id']
            session['current_space_name'] = space['name']
            return Response(rooms_and_sections, mimetype='application/json')
    session['logged'] = False
    session.clear()
    return 'not allowed', 405

@socketio.on('space_invite')
def space_invite():
    invite_only = collection_spaces.find_one({'_id': ObjectId(session['current_space'])})['invite_only']
    if ((not invite_only and space_member()) or (invite_only and (space_admin() or server_admin()))):
        invite = collection_invites.find_one({'space': session['current_space'], 'user': session['unique_id']})
        if invite != None:
            invite['exists'] = True
            for room in room_list():
                socketio.emit('space_invite', invite, room = room)
            return
        code = shortuuid.uuid()[:7]
        invite = {'_id': code, 'space': session['current_space'], 'picture': session['picture'], 'user': session['unique_id'], 'name': session['users_name'], 'email': session['users_email'], 'datetime': datetime.now().isoformat() + 'Z'}
        collection_invites.insert_one(invite)
        invite['exists'] = False
        for room in room_list():
            socketio.emit('space_invite', invite, room = room)
        return
    session['logged'] = False
    session.clear()
    emit('expired')

# @app.route('/space_invite', methods=['POST'])
# def space_invite():
#     if session_expired() or banned():
#         return 'expired', 200
#     invite_only = collection_spaces.find_one({'_id': ObjectId(session['current_space'])})['invite_only']
#     if ((not invite_only and space_member()) or (invite_only and (space_admin() or server_admin()))):
#         invite = collection_invites.find_one({'space': session['current_space'], 'user': session['unique_id']})
#         if invite != None:
#             return Response(dumps({'code': invite['_id']}), mimetype='application/json')
#         code = shortuuid.uuid()[:7]
#         collection_invites.insert_one({'_id': code, 'space': session['current_space'], 'picture': session['picture'], 'user': session['unique_id'], 'name': session['users_name'], 'email': session['users_email'], 'datetime': datetime.now().isoformat() + 'Z'})
#         return Response(dumps({'code': code}), mimetype='application/json')
#     session['logged'] = False
#     session.clear()
#     return 'not allowed', 405

# When user clicks leave space button, that space is removed
# from their list of joined spaces in MongoDB.
# Returns updated list of joined spaces.

@app.route('/leave_space', methods=['GET', 'POST'])
def leave_space():
    if session_expired() or banned():
        return 'expired', 200
    if request.method == 'POST' and not space_owner():
        collection_spaces.update_one({"_id": ObjectId(session['current_space'])}, { "$pull": {"members": {'$in': [session['unique_id']]}, 'admins': session['unique_id']}})
        joined = collection_users.find_one({"_id": session['unique_id']})['joined']
        joined.remove(session['current_space'])
        collection_users.update_one({"_id": session['unique_id']}, {"$set": {"joined": joined}})
        joined = dumps(joined)
        session['current_space'] = ''
        session['current_space_name'] = ''
        return Response(joined, mimetype='application/json')
    session['logged'] = False
    session.clear()
    return 'not allowed', 405

# Returns selected range of messages of the loaded room.
# Called either when user first loads a room
# or when more messages need to be loaded as user scrolls.

@app.route('/chat_history', methods=['GET', 'POST'])
def chat_history():
    if session_expired() or banned():
        return 'expired', 200
    if request.method == 'POST' and space_member():
        chat_history = dumps(list(collection_messages.find({'room': request.json['room_id']}).sort('_id', pymongo.DESCENDING).skip(int(request.json['i'])).limit(50)))
        return Response(chat_history, mimetype='application/json')
    session['logged'] = False
    session.clear()
    return 'not allowed', 405

@app.route('/email_history', methods=['GET', 'POST']) #return only emails users can see, and check if space admin
def email_history():
    if session_expired() or banned():
        return 'expired', 200
    if request.method == 'POST':
        email_history = collection_emails.find({'room': request.json['room_id']}).sort('_id', pymongo.DESCENDING).skip(int(request.json['i'])).limit(50)
        email_list = []
        for email in email_history:
            if session['users_email'] in email['recipients'] or 'Everyone' in email['recipients'] or space_admin() or server_admin():
                email_list.append(email)
        return Response(dumps(email_list), mimetype='application/json')
    session['logged'] = False
    session.clear()
    return 'not allowed', 405

@app.route('/send_email', methods=['GET', 'POST'])
def send_email():
    if session_expired() or banned():
        return 'expired', 200
    if request.method == 'POST' and (space_admin() or server_admin()):
        sender_email = 'oneconnected.application@gmail.com'
        password = os.environ['EMAIL_ACCESS_PASSWORD']
        message = MIMEMultipart('alternative')
        message['Subject'] = request.json['subject'][:70]
        message['From'] =  'One Connected'
        recipients = request.json['to']
        stored_recipients = list(set(recipients))
        stored_recipients.reverse()
        if 'Everyone' in recipients:
            everyone = collection_users.find({'joined': session['current_space']})
            for recipient in everyone:
                recipients.append(recipient['email'])
        recipients.append(session['users_email'])
        recipients = list(set(recipients))
        text = (request.json['message'][:10000].replace('\n', '<br />') + '<br>' +
        '--------------------------------------<br>' +
        session['users_name'] + '<br>' + 
        session['users_email'] + '<br>' +
        '<a href="https://www.oneconnected.app/school/' + session['current_space'] + '">' + session['current_space_name'] + '</a><br>' +
        '--------------------------------------<br>' +
        '<div style="color:lightgray;">do not reply</div>')
        message.attach(MIMEText(text, 'html'))
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=context) as server:
            server.login(sender_email, password)
            server.sendmail(sender_email, recipients, message.as_string())
        collection_emails.insert_one({'name': session['users_name'], 'picture': session['picture'], 'room': request.json['room_id'], 'email': session['users_email'], 'datetime': datetime.now().isoformat() + 'Z', 'from': session['unique_id'], 'recipients': stored_recipients, 'subject': request.json['subject'][:70], 'message': request.json['message'][:10000]})
        return Response(dumps({'success': 'true'}), mimetype='application/json')
    session['logged'] = False
    session.clear()
    return 'not allowed', 405

# Deletes the room and all of its messages in MongoDB. 

@app.route('/delete_room', methods=['GET', 'POST'])
def delete_room():
    if session_expired() or banned():
        return 'expired', 200
    if request.method == 'POST' and (space_admin() or server_admin()):
        room_count = collection_rooms.count_documents({'space': session['current_space']}) - collection_rooms.count_documents({'space': session['current_space'], 'section': 'special'})
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
    session['logged'] = False
    session.clear()
    return 'not allowed', 405

# Adds the newly created room to MongoDB.
# Returns the room data.

@app.route('/create_room', methods=['GET', 'POST'])
def create_room():
    if session_expired() or banned():
        return 'expired', 200
    if request.method == 'POST' and (space_admin() or server_admin()):
        room_id = ObjectId()
        room_list = list(collection_rooms.find({'space': session['current_space'], 'section': request.json['section_id']}))
        room = {'_id': room_id, 'space': session['current_space'], 'section': request.json['section_id'], 'name': request.json['room_name'][:200], 'order': len(room_list) + 1}
        collection_rooms.insert_one(room)
        room = dumps(room)
        return Response(room, mimetype='application/json')
    session['logged'] = False
    session.clear()
    return 'not allowed', 405

# Adds the newly created section to MongoDB.
# Returns the section data.

@app.route('/create_section', methods=['GET', 'POST'])
def create_section():
    if session_expired() or banned():
        return 'expired', 200
    if request.method == 'POST' and (space_admin() or server_admin()):
        section_id = ObjectId()
        section_list = list(collection_sections.find({'space': session['current_space']}))
        section = {'_id': section_id, 'space': session['current_space'], 'name': request.json['section_name'][:200], 'order': len(section_list) + 1}
        collection_sections.insert_one(section)
        section = dumps(section)
        return Response(section, mimetype='application/json')
    session['logged'] = False
    session.clear()
    return 'not allowed', 405

# Deletes a section from MongoDB.
# Updates the order and positioning of the other 
# sections and rooms in the space.

@app.route('/delete_section', methods=['GET', 'POST'])
def delete_section():
    if session_expired() or banned():
        return 'expired', 200
    if request.method == 'POST' and (space_admin() or server_admin()):
        section_count = collection_sections.count_documents({'space': session['current_space']})
        if section_count > 1:
            collection_sections.delete_one({'_id': ObjectId(request.json['section_id'])})
            order = 1
            section_list = collection_sections.find({'space': session['current_space']}).sort('order', 1)
            for section in section_list:
                collection_sections.update_one({'_id': section['_id']}, {'$set': {'order': order}})
                order += 1
            first_section = str(collection_sections.find_one({'space': session['current_space'], 'order': 1})['_id'])
            order = collection_rooms.count_documents({'section': first_section}) + 1
            room_list = collection_rooms.find({'section': request.json['section_id']}).sort('order', 1)
            for room in room_list:
                collection_rooms.update_one({'_id': room['_id']}, {'$set': {'order': order, 'section': first_section}})
                order += 1
            return Response(dumps({'success': 'true'}), mimetype='application/json')
        else:
            return Response(dumps({'success': 'false'}), mimetype='application/json')
    session['logged'] = False
    session.clear()
    return 'not allowed', 405

# Adds the newly created space, default room, and default
# section to MongoDB.
# Returns the room and section data.


@app.route('/create_space', methods=['GET', 'POST']) #Check if space with name already exists...
def create_space():
    if session_expired() or banned():
        return 'expired', 200
    user = collection_users.find_one({'_id': request.json['request_owner_id']})
    if request.method == 'POST' and user['owns'] < 5:
        space_id = ObjectId()
        room_id = ObjectId()
        email_room_id = ObjectId()
        section_id = ObjectId()
        room = {'_id': room_id, 'space': str(space_id), 'section': str(section_id), 'name': 'general', 'order': 1}
        special_rooms = {'_id': email_room_id, 'space': str(space_id), 'section': 'special', 'name': 'Email', 'order': 1}
        section = {'_id': section_id, 'space': str(space_id), 'name': 'discussion', 'order': 1}
        space_image = request.json['space_image']
        try:
            if not requests.head(space_image).headers["content-type"] in ("image/png", "image/jpeg", "image/jpg", "image/gif", "image/avif", "image/webp", "image/svg") or int(requests.get(space_image, stream = True).headers['Content-length']) > 6000000:
                space_image = '/static/images/Space.jpeg'
        except:
            space_image = '/static/images/Space.jpeg'
        collection_spaces.insert_one({'_id': space_id, 'name': request.json['space_name'][:200], 'picture': space_image, 'description': request.json['space_description'][:200], 'admins': [request.json['request_owner_id']], 'members': [[request.json['request_owner_id'], user['name']]], 'banned': [], 'theme': 'default', 'invite_only': False})
        collection_rooms.insert_many([room, special_rooms])
        collection_sections.insert_one(section)        
        joined = user['joined']
        joined.append(str(space_id))
        collection_users.find_one_and_update({"_id": request.json['request_owner_id']}, {'$set': {'joined': joined, 'owns': user['owns'] + 1}})
        return Response(dumps({'space_id': str(space_id), 'space_image': space_image}), mimetype='application/json')
    session['logged'] = False
    session.clear()
    return 'not allowed', 405

# Adds space to user's list of joined spaces in MongoDB.
# Returns space data.
# TODO: Check if space still exists in MongoDB.

@app.route('/delete_space', methods=['GET', 'POST'])
def delete_space():
    if session_expired() or banned():
        return 'expired', 200
    if request.method == 'POST' and (space_owner() or server_admin()):
        space = collection_spaces.find_one({'_id': ObjectId(session['current_space'])})
        collection_spaces.delete_one({'_id': ObjectId(session['current_space'])})
        collection_users.find_one_and_update({"_id": space['admins'][0]}, {'$inc': {'owns': -1}})
        for member in space['members']:
            joined = collection_users.find_one({"_id": member[0]})['joined']
            joined.remove(session['current_space'])
            collection_users.update_one({"_id": member[0]}, {"$set": {"joined": joined}})
        session['current_space_name'] = ''
        return Response(dumps({'success': 'true'}), mimetype='application/json')
    session['logged'] = False
    session.clear()
    return 'not allowed', 405

@app.route('/join_space', methods=['GET', 'POST'])
def join_space():
    if session_expired() or banned():
        return 'expired', 200
    space = collection_spaces.find_one({'_id': ObjectId(request.json['space_id'])})
    if space == None:
        return Response(dumps({'exists': 'false'}), mimetype='application/json')
    if session['unique_id'] in space['banned']:
        return Response(dumps({'ban': 'true'}), mimetype='application/json')
    joined = collection_users.find_one({"_id": session['unique_id']})['joined']
    if request.json['space_id'] not in joined:
        if space['invite_only'] and 'code' not in session and not server_admin():
            return Response(dumps({'only_invite': 'true'}), mimetype='application/json')
        if space['invite_only'] and 'code' in session and request.json['space_id'] != session['code'] and not server_admin():
            session.pop('code')
            return Response(dumps({'invalid_invite': 'true'}), mimetype='application/json')
        joined.append(request.json['space_id'])
        collection_users.find_one_and_update({"_id": session['unique_id']}, {'$set': {'joined': joined}})
        collection_spaces.find_one_and_update({"_id": ObjectId(request.json['space_id'])}, {'$push': {'members': [session['unique_id'], session['users_name']]}})
    return Response(dumps(space), mimetype='application/json')

# When user deletes a message, delete that message from MongoDB.
# If the combine status of the next message is true, then
# change the combine status of it to false.
# TODO: Add deleted message to the report collection in MongoDB.

# When a message is deleted, send that message data to all
# users in the space.

@socketio.on('delete_message')
def deleted_message(data):
    if session_expired() or banned():
        emit('expired')
        return 
    if space_admin() or server_admin() or session['unique_id'] == collection_messages.find_one({'_id': ObjectId(data['message_id']['user_id'])}):
        socketio.emit('deleted_message', data, room = data['room_id'])
    else:
        session['logged'] = False
        session.clear()
        emit('expired')
        
@app.route('/delete_message', methods=['GET', 'POST']) #space admin and message in space
def delete_message():
    if session_expired() or banned():
        return 'expired', 200
    if request.method == 'POST':
        deleted_message = collection_messages.find_one({'_id': ObjectId(request.json['message_id'])})
        if deleted_message['user_id'] == session['unique_id'] or space_admin() or server_admin():
            deleted_email = collection_messages.find({'room': request.json['room_id'], 'email': deleted_message['email']}).sort('_id', pymongo.DESCENDING)
            document_list = list(deleted_email)
            message_index = document_list.index(deleted_message)
            if message_index != 0:
                if document_list[message_index-1]["combine"] == "true" and document_list[message_index]["combine"] == "false":
                    collection_messages.find_one_and_update({'_id': ObjectId(document_list[message_index-1]['_id'])}, {'$set': {'combine': 'false'}}) 
            #collection_deleted.insert_one({'name': session['users_email'], 'datetime': datetime.now().isoformat() + 'Z', 'deleted_message_content': deleted_message}) Used to add to logs once deleted.
            collection_messages.delete_one({"_id": ObjectId(request.json['message_id'])})
            collection_logs.insert_one({'name': session['users_name'], 'user_id': session['unique_id'], 'email': session['users_email'], 'action': 'deleted message', 'by': deleted_message['name'], 'by_email': deleted_message['email'], 'in': session['current_space_name'], 'space_id': session['current_space'], 'details': deleted_message, 'datetime': datetime.now().isoformat() + 'Z'})
            return Response(dumps({'success': message_index}), mimetype='application/json')
    session['logged'] = False
    session.clear()
    return 'not allowed', 405

# When a user reports a message, add a report with
# relevant information to MongoDB.
# TODO: Store more information like reported time, reason for report, etc.

@app.route('/report_message', methods=['GET', 'POST'])
def report_message():
    if session_expired() or banned():
        return 'expired', 200
    if request.method == 'POST' and space_member():
        reported_message = collection_messages.find_one({'_id': ObjectId(request.json['message_id'])})
        if collection_logs.count_documents({'details._id': reported_message['_id']}) == 0:
            collection_logs.insert_one({'name': session['users_name'], 'user_id': session['unique_id'], 'email': session['users_email'], 'action': 'reported message', 'by': reported_message['name'], 'by_email': reported_message['email'], 'in': session['current_space_name'], 'space_id': session['current_space'], 'details': reported_message, 'datetime': datetime.now().isoformat() + 'Z', 'note': request.json['note'][:800]})
            return Response(dumps({'success': 'true'}), mimetype='application/json')
        else:
            return Response(dumps({'success': 'many'}), mimetype='application/json')
    session['logged'] = False
    session.clear()
    return 'not allowed', 405

#When a user submits a space creation request, add the request to the database.
#If user is deleted from database, space request remains. Fix later maybe.

@app.route('/approve_space', methods=['GET', 'POST'])
def approve_space():
    if session_expired() or banned():
        return 'expired', 200
    if request.method == 'POST':
        try:
            if not requests.head(request.json['space_name']).headers["content-type"] in ("image/png", "image/jpeg", "image/jpg", "image/gif", "image/avif", "image/webp", "image/svg") or int(requests.get(request.json['space_name'], stream = True).headers['Content-length']) > 6000000:
                request.json['space_image'] = '/static/images/Space.jpeg'
        except:
            request.json['space_image'] = '/static/images/Space.jpeg'
        collection_spaceRequests.insert_one({'by': session['users_name'], 'picture': session['picture'], 'user_id': session['unique_id'], 'email': session['users_email'], 'space_name': request.json['space_name'], 'space_image': request.json['space_image'], 'space_description': request.json['space_description']})
        return Response(dumps({'success': 'true'}), mimetype='application/json')
    session['logged'] = False
    session.clear()
    return 'not allowed', 405

@app.route('/confirm_approve_space', methods=['GET', 'POST'])
def confirm_approve_space():
    if session_expired() or banned():
        return 'expired', 200
    if request.method == 'POST':
        space_data = collection_spaceRequests.find_one({'_id': ObjectId(request.json['request_id'])})
        collection_spaceRequests.delete_one({'_id': ObjectId(request.json['request_id'])})
        return Response(dumps(space_data), mimetype='application/json')
    session['logged'] = False
    session.clear()
    return 'not allowed', 405
    
# When user accesses another user's profile,
# return their public profile data.

@app.route('/open_member_profile', methods=['GET', 'POST'])
def member_profile():
    if session_expired() or banned():
        return 'expired', 200
    if request.method == 'POST':
        member = collection_users.find_one({'_id': request.json['user_id']})
        queried_spaces = []
        names_list = []
        for space_id in member['joined']:
            try: 
                space = collection_spaces.find_one({'_id': ObjectId(space_id)})
                queried_spaces.append([space['picture'], space_id, space['name']])
                names_list.append(space['name'])
            except:
                pass
        user_data = {'name': member['name'], 'email': member['email'], 'picture': member['picture'], 'joined': queried_spaces, 'joined_spaces_names': names_list}
        return Response(dumps(user_data), mimetype='application/json')
    session['logged'] = False
    session.clear()
    return 'not allowed', 405

# When user accesses their own profile,
# return all of their personal profile data.

@app.route('/profile', methods=['GET', 'POST'])
def profile():
    if session_expired() or banned():
        return 'expired', 200
    if request.method == 'POST':
        data = collection_users.find_one({'_id': session['unique_id']})
        return Response(dumps(data), mimetype='application/json')
    session['logged'] = False
    session.clear()
    return 'not allowed', 405

@app.route('/sorted_spaces', methods=['GET', 'POST'])
def sorted_spaces():
    if session_expired() or banned():
        return 'expired', 200
    if request.method == 'POST':
        collection_users.find_one({"_id": session['unique_id']})['joined']
        if sorted(collection_users.find_one({"_id": session['unique_id']})['joined']) == sorted(request.json['space_list']):
            collection_users.find_one_and_update({"_id": session['unique_id']}, {'$set': {'joined': request.json['space_list']}})
            return Response(dumps({'success': 'true'}), mimetype='application/json')
    session['logged'] = False
    session.clear()
    return 'not allowed', 405

# TODO: tokenize and make queries more efficient

@app.route('/server_logs', methods=['GET', 'POST'])
def server_logs():
    if session_expired() or banned():
        return 'expired', 200
    if request.method == 'POST' and server_admin():
        logs = None
        search = '.*' + request.json['options'][0] + '.*'
        options = []
        if request.json['options'][1]:
            options.append('reported message')
        if request.json['options'][2]:
            options.append('deleted message')
        if request.json['options'][3]:
            options.append('edited message')
        if len(options) != 0:
            logs = dumps(list(collection_logs.find({'$and': [{'action': {'$in': options}}, {'$or': [{'name': {'$regex': search}}, {'email': {'$regex': search}}, {'by': {'$regex': search}}, {'by_email': {'$regex': search}}, {'in': {'$regex': search}}, {'details.message': {'$regex': search}}]}]}).sort('_id', pymongo.DESCENDING).skip(int(request.json['i'])).limit(50)))
        return Response(logs, mimetype='application/json')
    session['logged'] = False
    session.clear()
    return 'not allowed', 405

@app.route('/server_users', methods=['GET', 'POST'])
def server_users():
    if session_expired() or banned():
        return 'expired', 200
    if request.method == 'POST' and server_admin():
        users = dumps(list(collection_users.find().sort('_id', pymongo.DESCENDING)))
        return Response(users, mimetype='application/json')
    session['logged'] = False
    session.clear()
    return 'not allowed', 405

@app.route('/server_spaceApproval', methods=['GET', 'POST'])
def server_spaceApproval():
    if session_expired() or banned():
        return 'expired', 200
    if request.method == 'POST' and server_admin():
        requests = dumps(list(collection_spaceRequests.find().sort('_id', pymongo.DESCENDING)))
        return Response(requests, mimetype='application/json')
    session['logged'] = False
    session.clear()
    return 'not allowed', 405

@app.route('/admin_change_user_status', methods=['GET', 'POST'])
def admin_change_user_status():
    if session_expired() or banned():
        return 'expired', 200
    if request.method == 'POST' and server_admin() and collection_users.find_one({'_id': request.json['user_id']})['status'] != 'owner':
        if request.json['status'] in {'user', 'admin'}:
            collection_users.find_one_and_update({"_id": request.json['user_id']}, {'$set': {'status': request.json['status']}})
            if request.json['user_id'] == session['unique_id'] and request.json['status'] == 'user':
                session.clear()
                return Response(dumps({'success': 'self'}), mimetype='application/json')
            return Response(dumps({'success': 'true'}), mimetype='application/json')
        elif request.json['status'] == 'banned' and collection_users.find({'_id': request.json['user_id']})['status'] not in {'admin', 'owner'}:
            collection_users.find_one_and_update({"_id": request.json['user_id']}, {'$set': {'status': 'banned'}})
            return Response(dumps({'success': 'true'}), mimetype='application/json')
    session['logged'] = False
    session.clear()
    return 'not allowed', 405

@socketio.on('change_user_status')
def change_user_status(data):
    if server_admin() or space_admin():
        data['user'] = session['unique_id']
        space = collection_spaces.find_one({'_id': ObjectId(session['current_space'])})
        if (space_admin() or server_admin()) and data['user_id'] != space['admins'][0]:
            user = collection_users.find_one({'_id': data['user_id']})
            if data['status'] == 'banned' and data['user_id'] not in space['banned'] and data['user_id'] != space['admins'][0] and user['status'] != 'admin' and user['status'] != 'owner':
                collection_spaces.update_one({"_id": ObjectId(session['current_space'])}, {"$pull": {"members": {'$in': [data['user_id']]}, 'admins': data['user_id']}})
                collection_spaces.update_one({'_id': ObjectId(session['current_space'])}, {'$push': {'banned': data['user_id']}})
                collection_users.update_one({'_id': data['user_id']}, {'$pull': {'joined': session['current_space']}})
                for room in room_list():
                    socketio.emit('change_user_status', data, room = room)
                return
            elif data['status'] == 'moderator' and data['user_id'] not in space['admins']:
                collection_spaces.update_one({'_id': ObjectId(session['current_space'])}, {'$push': {'admins': data['user_id']}})
                for room in room_list():
                    socketio.emit('change_user_status', data, room = room)
                return
            elif data['status'] == 'member':
                collection_spaces.update_one({"_id": ObjectId(session['current_space'])}, {"$pull": {'banned': data['user_id'], 'admins': data['user_id']}})
                for room in room_list():
                    socketio.emit('change_user_status', data, room = room)
                return
    session['logged'] = False
    session.clear()
    emit('expired')

@app.route('/change_user_status', methods=['POST'])
def change_user_status():
    if session_expired() or banned():
        return 'expired', 200
    space = collection_spaces.find_one({'_id': ObjectId(session['current_space'])})
    if (space_admin() or server_admin()) and request.json['user_id'] != space['admins'][0]:
        user = collection_users.find_one({'_id': request.json['user_id']})
        if request.json['status'] == 'banned' and request.json['user_id'] not in space['banned'] and request.json['user_id'] != space['admins'][0] and user['status'] != 'admin' and user['status'] != 'owner':
            collection_spaces.update_one({"_id": ObjectId(session['current_space'])}, {"$pull": {"members": {'$in': [request.json['user_id']]}, 'admins': request.json['user_id']}})
            collection_spaces.update_one({'_id': ObjectId(session['current_space'])}, {'$push': {'banned': request.json['user_id']}})
            collection_users.update_one({'_id': request.json['user_id']}, {'$pull': {'joined': session['current_space']}})
            return Response(dumps({'success': 'true'}), mimetype='application/json')
        elif request.json['status'] == 'moderator' and request.json['user_id'] not in space['admins']:
            collection_spaces.update_one({'_id': ObjectId(session['current_space'])}, {'$push': {'admins': request.json['user_id']}})
            return Response(dumps({'success': 'true'}), mimetype='application/json')
        elif request.json['status'] == 'member':
            collection_spaces.update_one({"_id": ObjectId(session['current_space'])}, {"$pull": {'banned': request.json['user_id'], 'admins': request.json['user_id']}})
            return Response(dumps({'success': 'true'}), mimetype='application/json')
    session['logged'] = False
    session.clear()
    return 'not allowed', 405
    
@socketio.on('edit_space_profile')
def edit_space_profile(data):
    if server_admin() or space_admin():
        data['space_picture'] = data['space_picture'].strip()
        try:
            if not requests.head(data['space_picture']).headers["content-type"] in ("image/png", "image/jpeg", "image/jpg", "image/gif", "image/avif", "image/webp", "image/svg") or int(requests.get(data['space_picture'], stream = True).headers['Content-length']) > 6000000:
                data['space_picture'] = '/static/images/Space.jpeg'
        except:
            data['space_picture'] = '/static/images/Space.jpeg'
        data['space_name'] = data['space_name'][:200].strip()
        data['space_description'] = data['space_description'][:200].strip()
        data['user_id'] = session['unique_id']
        collection_spaces.find_one_and_update({'_id': ObjectId(session['current_space'])}, {'$set': {'name': data['space_name'], 'picture': data['space_picture'], 'description': data['space_description']}})
        for room in room_list():
            socketio.emit('edit_space_profile', data, room = room)
        return
    session['logged'] = False
    session.clear()
    emit('expired')

@app.route('/edit_space_profile', methods=['POST'])
def edit_space_profile():
    if session_expired() or banned():
        return 'expired', 200
    if server_admin() or space_admin():
        space_picture = request.json['space_picture'].strip()
        try:
            if not requests.head(space_picture).headers["content-type"] in ("image/png", "image/jpeg", "image/jpg", "image/gif", "image/avif", "image/webp", "image/svg") or int(requests.get(space_picture, stream = True).headers['Content-length']) > 6000000:
                space_picture = '/static/images/Space.jpeg'
        except:
            space_picture = '/static/images/Space.jpeg'
        collection_spaces.find_one_and_update({'_id': ObjectId(session['current_space'])}, {'$set': {'name': request.json['space_name'][:200].strip(), 'picture': space_picture, 'description': request.json['space_description'][:200].strip()}})
        return Response(dumps({'space_name': request.json['space_name'][:200].strip(), 'space_picture': space_picture, 'space_description': request.json['space_description'][:200].strip()}), mimetype='application/json')
    session['logged'] = False
    session.clear()
    return 'not allowed', 405

@socketio.on('edit_space_invite_switch')
def edit_space_invite_switch(data):
    if server_admin() or space_admin():
        collection_spaces.update_one({'_id': ObjectId(session['current_space'])}, {'$set': {'invite_only': data['invite_only']}})
        data['user_id'] = session['unique_id']
        for room in room_list():
            socketio.emit('edit_space_invite_switch', data, room = room)
        # return Response(dumps({'success': 'true'}), mimetype='application/json')
        return
    session['logged'] = False
    session.clear()
    emit('expired')
    
    

# @app.route('/edit_space_invite', methods=['POST'])
# def edit_space_invite():
#     if session_expired() or banned():
#         return 'expired', 200
#     if server_admin() or space_admin():
#         collection_spaces.find_one_and_update({'_id': ObjectId(session['current_space'])}, {'$set': {'invite_only': request.json['invite_only']}})
#         return Response(dumps({'success': 'true'}), mimetype='application/json')
#     session['logged'] = False
#     session.clear()
#     return 'not allowed', 405

@socketio.on('revoke_link')
def revoke_invite(data):
    if server_admin() or space_admin():
        collection_invites.delete_one({'_id': data['invite_id']})
        for room in room_list():
            socketio.emit('revoke_link', data, room = room)
    else:
        session['logged'] = False
        session.clear()
        emit('expired')
    
# When a room is clicked, make user join room
# and leave old room.

@socketio.on('join_room')
def change_room(data):
    if space_member() and valid_room(data['room_id']):
        try: 
            leave_room(data['old_room'])
        except:
            pass
        join_room(data['room_id'])
    else:
        session['logged'] = False
        session.clear()
        emit('expired')

# When user starts typing, notify all users in that room.

@socketio.on('is_typing')
def is_typing(data):
    if space_member() and valid_room(data['room_id']):
        socketio.emit('is_typing', data, room = data['room_id'])
    else:
        session['logged'] = False
        session.clear()
        emit('expired')

# When user stops typing, notify all users in that room.

@socketio.on('stopped_typing')
def stopped_typing(data):
    if space_member() and valid_room(data['room_id']):
        socketio.emit('stopped_typing', data, room = data['room_id'])
    else:
        session['logged'] = False
        session.clear()
        emit('expired')

# When a message is sent, verify and store it in MongoDB.
# Send the message data to all users in that room.

@socketio.on('send_message')
def send_message(data):
    profanity.load_censor_words();
    if session_expired() or banned():
        emit('expired')
        return
    if space_member() and valid_room(data['room_id']):
        utc_dt = datetime.now().isoformat() + 'Z'
        data['datetime'] = utc_dt
        data['message'] = profanity.censor(re.sub('\\\n\\n\\\n+', '\\n\\n', data['message'][:2000]), '#')
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
    else:
        session['logged'] = False
        session.clear()
        emit('expired')
    
# When a room is created, send that room data to all
# users in the space.

@socketio.on('created_room')
def created_room(data):
    if space_admin() or server_admin():
        for room in room_list():
            socketio.emit('created_room', data, room = room)
    else:
        session['logged'] = False
        session.clear()
        emit('expired')

# When a room is deleted, send that room data to all
# users in the space.

@socketio.on('deleted_room')
def deleted_room(data):
    if space_admin() or server_admin():
        for room in room_list():
            socketio.emit('deleted_room', data, room = room)
    else:
        session['logged'] = False
        session.clear()
        emit('expired')

@socketio.on('deleted_space')
def deleted_space():
    for room in room_list():
        socketio.emit('deleted_space', room = room)
    session['current_space'] = ''

# When a section is created, send that section data to all
# users in the space.

@socketio.on('created_section')
def created_section(data):
    if space_admin() or server_admin():
        for room in room_list():
            socketio.emit('created_section', data, room = room)
    else:
        session['logged'] = False
        session.clear()
        emit('expired')

# When a section is deleted, send that section data to all
# users in the space.

@socketio.on('deleted_section')
def created_section(data):
    if space_admin() or server_admin():
        for room in room_list():
            socketio.emit('deleted_section', data, room = room)
    else:
        session['logged'] = False
        session.clear()
        emit('expired')

# When a message is edited, update the message in MongoDB and
# send the message data to all users in that room.

@socketio.on('edited_message')
def edited_message(data):
    if session_expired() or banned():
        emit('expired')
        return
    if space_admin() or server_admin() or session['unique_id'] == collection_messages.find_one({'_id': ObjectId(data['message_id'])})['user_id']:
        edited_message = collection_messages.find_one_and_update({"_id": ObjectId(data['message_id'])}, {'$set': {'message': data['edit'][:2000], 'edited': True}})
        collection_logs.insert_one({'name': session['users_name'], 'user_id': session['unique_id'], 'email': session['users_email'], 'action': 'edited message', 'by': edited_message['name'], 'by_email': edited_message['email'], 'in': session['current_space_name'], 'space_id': session['current_space'], 'details': edited_message, 'datetime': datetime.now().isoformat() + 'Z'})
        socketio.emit('edited_message', data, room = data['room_id'])
    else:
        session['logged'] = False
        session.clear()
        emit('expired')
# When sections are sorted, update the order in MongoDB.

@socketio.on('sorted_sections')
def sorted_sections(data):
    if space_admin() or server_admin():
        for section in data['section_list']:
            collection_sections.find_one_and_update({"_id": ObjectId(section)}, {'$set': {'order': data['section_list'].index(section) + 1}})
        for room in room_list():
            socketio.emit('sorted_sections', data, room = room)
    else:
        session['logged'] = False
        session.clear()
        emit('expired')
    
# When rooms are sorted, update the order in MongoDB.

@socketio.on('sorted_rooms')
def sorted_rooms(data):
    if space_admin() or server_admin():
        for section in data['room_group_list']:
            if len(section) > 1:
                for room in section[1:]:
                    if not valid_room(room):
                        return
        order = 1
        for section in data['room_group_list']:
            if len(section) > 1:
                for room in section[1:]:
                    collection_rooms.find_one_and_update({"_id": ObjectId(room)}, {'$set': {'order': order, 'section': section[0]}})
                    order += 1
                    socketio.emit('sorted_rooms', data, room = room)
            order = 1
    else:
        session['logged'] = False
        session.clear()
        emit('expired')

@socketio.on('sent_email')
def sent_email(data):
    if valid_room(data['room_id']) and (space_admin() or server_admin()):
        socketio.emit('sent_email', data, room = data['room_id'])
    else:
        session['logged'] = False
        session.clear()
        emit('expired')

@socketio.on('joined_space')
def joined_space():
    user = collection_users.find_one({'_id': session['unique_id']})
    for room in room_list():
        emit('joined_space', user, room = room, include_self=False)

@socketio.on('edit_channel')
def edit_channel(data):
    if space_admin() or server_admin():
        collection_rooms.find_one_and_update({'_id': ObjectId(data['room_id'])}, {'$set': {'name': data['room_name'].strip()}})
        for room in room_list():
            socketio.emit('edit_channel', data, room = room)
    else:
        session['logged'] = False
        session.clear()
        emit('expired')

@socketio.on('edit_section')
def edit_section(data):
    if space_admin() or server_admin():
        collection_sections.find_one_and_update({'_id': ObjectId(data['section_id'])}, {'$set': {'name': data['section_name'].strip()}})
        for room in room_list():
            socketio.emit('edit_section', data, room = room)
    else:
        session['logged'] = False
        session.clear()
        emit('expired')

@socketio.on('change_theme')
def change_theme(data):
    if space_admin() or server_admin():
        if data['theme'] in ('default', 'dark', 'nature'):
            collection_spaces.update_one({'_id': ObjectId(session['current_space'])}, {'$set': {'theme': data['theme']}})
            for room in room_list():
                socketio.emit('change_theme', data, room = room)
    else:
        session['logged'] = False
        session.clear()
        emit('expired')

def session_expired():
    if not session.get('logged'):
        return True
    return False

def space_admin():
    if session['unique_id'] in collection_spaces.find_one({'_id': ObjectId(session['current_space'])})['admins']:
        return True
    return False

def space_owner():
    if session['unique_id'] == collection_spaces.find_one({'_id': ObjectId(session['current_space'])})['admins'][0]:
        return True
    return False

def space_member():
    if any(session['unique_id'] in item for item in collection_spaces.find_one({'_id': ObjectId(session['current_space'])})['members']):
        return True
    return False

def server_admin():
    if collection_users.find_one({'_id': session['unique_id']})['status'] in ('admin', 'owner'):
        return True
    return False

def banned():
    if collection_users.find_one({'_id': session['unique_id']})['status'] == 'banned':
        session.clear()
        return True
    return False

def room_list():
    room_ids = []
    for room in list(collection_rooms.find({'space': session['current_space']})):
        room_ids.append(str(room['_id']))
    return room_ids

def valid_room(room_id):
    if session['current_space'] == collection_rooms.find_one({'_id': ObjectId(room_id)})['space']:
        return True
    return False
    
#if __name__ == '__main__':
#    socketio.run(app, debug=False)
