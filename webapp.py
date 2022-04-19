from flask_socketio import SocketIO, emit, join_room

import json
import os
import re

from flask import Flask, redirect, Markup, url_for, session, request, jsonify, Response, request
from flask import render_template

from oauthlib.oauth2 import WebApplicationClient
import requests
from flask_talisman import Talisman

from bson.objectid import ObjectId
from bson.json_util import dumps

#import pprint
#import sys
import pymongo
from datetime import datetime, date, timedelta
import pytz
from pytz import timezone

# GOOGLE_CLIENT_ID = os.environ['GOOGLE_CLIENT_ID']
GOOGLE_CLIENT_ID = '508195289716-ooj778o3qtc77j3lhef75ppqrfaru814.apps.googleusercontent.com'

GOOGLE_CLIENT_SECRET = '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' #os.environ['GOOGLE_CLIENT_SECRET']
GOOGLE_DISCOVERY_URL = (
    'https://accounts.google.com/.well-known/openid-configuration'
)

connection_string = 'mongodb+srv://first:EDqorw8lcCRVOMHI@cluster0.p7hdr.mongodb.net/myFirstDatabase?retryWrites=true&w=majority' #os.environ['MONGO_CONNECTION_STRING']
db_name = 'SBHSPlatform' #os.environ['MONGO_DBNAME']
client = pymongo.MongoClient(connection_string)
db = client[db_name]
collection_users = db['Users']
collection_spaces = db['Spaces']
collection_messages = db['Messages']

app = Flask(__name__)
app.secret_key = "?W6e{:*-RuaqEX2E0]$jTK(]HSc^|:2sfY~#jBbuz,BYH2yBt(66E7~j)')l@`a" #os.environ['SECRET_KEY']

socketio = SocketIO(app, async_mode='gevent')

client = WebApplicationClient(GOOGLE_CLIENT_ID)

@app.route('/') 
def render_login():
    
    # Renders the login page. If user has attempted to log in and failed, then it renders page with error message.

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
            return redirect(url_for('render_login', error = "Please use your school issued email"))
    else:
        return redirect(url_for('render_login', error = "Email not available or verified"))
    
    # Store user data in session
    
    session['unique_id'] = unique_id
    session['users_email'] = users_email
    session['picture'] = picture
    session['users_name'] = users_name
    
    # Store user data in MongoDB if new user.
    
    if not collection_users.count_documents({ '_id': unique_id}, limit = 1):
        collection_users.insert_one({'_id': unique_id, 'name': users_name, 'email': users_email, 'picture': picture}) #check if profile picture the same !
        
    return redirect(url_for('render_main_page'))

def get_google_provider_cfg():
    
    # Handle errors to Google API call.
    
    return requests.get(GOOGLE_DISCOVERY_URL).json()
    
@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('render_login'))

@socketio.on('join_room')
def join(data):
    join_room(data['room'])
    #socketio.emit('join_room_announcement', data, room = data['room'])
    
@socketio.on('send_message')
def send_message(data):
    utc_dt = datetime.now().isoformat() + 'Z'
    data['datetime'] = utc_dt
    data['message'] = re.sub('\\\n\\n\\\n+', '\\n\\n', data['message'])
    latest_message = collection_messages.find_one({'room': data['room']}, sort=[( '_id', pymongo.DESCENDING )])
    duration = datetime.now() - datetime.fromisoformat(latest_message.get('datetime').replace('Z', ''))
    if latest_message.get('name') == session['users_name'] and latest_message.get('picture') == session['picture'] and duration.total_seconds() < 180:
        data['combine'] = 'true'
    else:
        data['combine'] = 'false'
    collection_messages.insert_one({'name': data['name'], 'picture': session['picture'], 'room': data['room'], 'datetime': utc_dt, 'message': data['message'], 'combine': data['combine']})
    socketio.emit('recieve_message', data, room = data['room'])
    
@app.route('/sbhs')
def render_main_page():
    #when creating the list of all the spaces, make sure they all have their own unique IDs stored
    #collection_users = 
    #return render_template('index.html', name = session['users_name'], room = '1', picture = session['picture'])
    return render_template('home.html')#, username = session['users_name'], room = '1')

@app.route('/chat_history', methods=['GET', 'POST'])
def chat_history():
    if request.method == 'POST': #get data for the room that user is currently in
        chat_history = dumps(list(collection_messages.find({'room': '1'}).sort('_id', pymongo.DESCENDING).skip(int(request.json['i'])).limit(100))) #LIMITs,
        return Response(chat_history, mimetype='application/json')

@app.route('/space', methods=['GET', 'POST'])#/<space_id>')
def render_space():
    if request.method == 'POST':
        results = {'processed': 'true'}
        return jsonify(results)
        return render_template('index.html', username = session['users_name'], room = '1')

if __name__ == '__main__':
    socketio.run(app, debug=False)
