import socket
import threading
import requests
import time
import re
import os
import json
from urllib.parse import urlencode, urlparse, parse_qs
from flask import Flask, request, jsonify
import hmac
import hashlib

# Twitch and OpenAI configuration
TWITCH_BOT_USERNAME = "YourBotUsername"
TWITCH_CLIENT_ID = "your_twitch_client_id"  # Replace with your actual Client ID
TWITCH_CLIENT_SECRET = "your_twitch_client_secret"  # Replace with your actual Client Secret
TWITCH_CHANNEL = "your_channel_name"  # Replace with your channel name (lowercase)
OPENAI_API_KEY = "your_openai_api_key"  # Replace with your actual OpenAI API key
TWITCH_IRC_SERVER = "irc.chat.twitch.tv"
TWITCH_IRC_PORT = 6667
TWITCH_WEBHOOK_SECRET = "your_webhook_secret"  # Secret for verifying EventSub messages
WEBHOOK_URL = "https://your_public_domain.com/webhook"  # Replace with your public webhook URL

# Global variables
ACCESS_TOKEN = None
TOKEN_EXPIRY = None
IRC_SOCKET = None
game_state = None
suspect_count = {}
murderer_name = ''  # Store the murderer's name for comparison
cooldown = 300  # Cooldown time in seconds between mysteries
last_mystery_time = 0  # Timestamp of the last mystery
app = Flask(__name__)

# Function to get the app access token using OAuth Client Credentials Flow
def get_app_access_token():
    global ACCESS_TOKEN, TOKEN_EXPIRY
    url = 'https://id.twitch.tv/oauth2/token'
    params = {
        'client_id': TWITCH_CLIENT_ID,
        'client_secret': TWITCH_CLIENT_SECRET,
        'grant_type': 'client_credentials'
    }
    response = requests.post(url, params=params)
    if response.status_code == 200:
        data = response.json()
        ACCESS_TOKEN = data['access_token']
        expires_in = data['expires_in']
        TOKEN_EXPIRY = time.time() + expires_in
        print("Successfully obtained app access token.")
    else:
        print(f"Failed to obtain app access token: {response.text}")

# Function to refresh the access token if expired
def refresh_access_token_if_needed():
    if ACCESS_TOKEN is None or time.time() > TOKEN_EXPIRY:
        get_app_access_token()

# Function to send a message to Twitch chat
def send_message(sock, message):
    max_length = 490  # Twitch's limit is 500; we use 490 to be safe.
    lines = message.split('\n')
    for line in lines:
        line = line.strip()
        if line:
            while len(line) > 0:
                if len(line) <= max_length:
                    send_line = line
                    line = ''
                else:
                    # Find the last space before max_length to avoid splitting words
                    split_index = line.rfind(' ', 0, max_length)
                    if split_index == -1:
                        split_index = max_length
                    send_line = line[:split_index]
                    line = line[split_index:].lstrip()
                print(f"Sending message to chat: {send_line}")
                sock.send(f"PRIVMSG #{TWITCH_CHANNEL} :{send_line}\r\n".encode('utf-8'))
                time.sleep(2)  # Delay to avoid rate limits

# Function to connect to Twitch IRC
def connect_to_twitch():
    global IRC_SOCKET
    refresh_access_token_if_needed()
    IRC_SOCKET = socket.socket()
    IRC_SOCKET.connect((TWITCH_IRC_SERVER, TWITCH_IRC_PORT))
    IRC_SOCKET.send(f"PASS oauth:{ACCESS_TOKEN}\r\n".encode('utf-8'))
    IRC_SOCKET.send(f"NICK {TWITCH_BOT_USERNAME}\r\n".encode('utf-8'))
    IRC_SOCKET.send(f"JOIN #{TWITCH_CHANNEL}\r\n".encode('utf-8'))
    print("Connected to Twitch IRC successfully.")

# Function to fetch a mystery from ChatGPT
def fetch_mystery_from_chatgpt():
    headers = {
        'Content-Type': 'application/json',
        'Authorization': f'Bearer {OPENAI_API_KEY}'
    }
    data = {
        "model": "gpt-4",  # Use 'gpt-4' or 'gpt-3.5-turbo' depending on availability
        "messages": [
            {
                "role": "system",
                "content": "You are a murder mystery generator bot."
            },
            {
                "role": "user",
                "content": (
                    "Generate a difficult but solvable murder mystery that includes subtle interesting clues throughout with the following exact format:\n\n"
                    "Backstory: <backstory details>\n\n"
                    "The Murder: <murder details>\n\n"
                    "Suspects: <list of suspects>\n\n"
                    "Clue Phase: <clue details>\n\n"
                    "Murderer: <name of the murderer>\n\n"
                    "The Reveal: <reveal the murderer and reasoning>\n\n"
                    "Ensure each section is clearly labeled and avoid any additional formatting like asterisks or underscores."
                )
            }
        ]
    }

    try:
        print("Sending request to ChatGPT API...")
        response = requests.post(
            'https://api.openai.com/v1/chat/completions',
            headers=headers,
            json=data
        )
        if response.status_code == 200:
            mystery_response = response.json()
            print("Received response from ChatGPT.")
            mystery_text = mystery_response['choices'][0]['message']['content']
            return parse_mystery_response(mystery_text)
        else:
            print(f"Error fetching mystery: {response.status_code}")
            print(f"Response text: {response.text}")
            return None
    except Exception as e:
        print(f"Exception during API call to ChatGPT: {e}")
        return None

# Function to parse the mystery response
def parse_mystery_response(mystery_text):
    try:
        print("Parsing mystery response.")
        pattern = r"Backstory:\s*(.*?)\s*The Murder:\s*(.*?)\s*Suspects:\s*(.*?)\s*Clue Phase:\s*(.*?)\s*Murderer:\s*(.*?)\s*The Reveal:\s*(.*)"
        match = re.match(pattern, mystery_text, re.DOTALL | re.IGNORECASE)
        if match:
            backstory = match.group(1).strip()
            murder = match.group(2).strip()
            suspects = match.group(3).strip()
            clues = match.group(4).strip()
            murderer = match.group(5).strip()
            reveal = match.group(6).strip()
            return backstory, murder, suspects, clues, murderer, reveal
        else:
            print("Error: Mystery response does not match the expected format.")
            return "", "", "", "", "", ""
    except Exception as e:
        print(f"Error parsing the mystery response: {e}")
        return "", "", "", "", "", ""

# Function to receive messages from Twitch chat
def receive_messages():
    global game_state, suspect_count
    buffer = ""
    while True:
        try:
            response = IRC_SOCKET.recv(2048).decode('utf-8')
            buffer += response
            while '\r\n' in buffer:
                line, buffer = buffer.split('\r\n', 1)
                if line.startswith('PING'):
                    IRC_SOCKET.send('PONG\n'.encode('utf-8'))
                else:
                    process_chat_message(line)
        except Exception as e:
            print(f"Error receiving messages: {e}")
            break

# Function to process chat messages
def process_chat_message(line):
    global game_state, suspect_count, last_mystery_time, cooldown
    if "PRIVMSG" in line:
        match = re.search(r":(\w+)!.*PRIVMSG #\w+ :(.*)", line)
        if match:
            username = match.group(1)
            message = match.group(2)
            print(f"Message received from {username}: {message}")

            if message.lower() == "!mystery":
                current_time = time.time()
                if game_state is None:
                    if current_time - last_mystery_time >= cooldown:
                        threading.Thread(target=start_mystery).start()
                    else:
                        remaining_time = int(cooldown - (current_time - last_mystery_time))
                        send_message(IRC_SOCKET, f"Please wait {remaining_time} seconds before starting a new mystery.")
                else:
                    send_message(IRC_SOCKET, "A mystery is already in progress.")
            elif game_state == 'guessing':
                suspect = message.strip().lower()
                if suspect:
                    suspect_count[suspect] = suspect_count.get(suspect, 0) + 1
                    # Fetch user info using Twitch API
                    user_info = get_user_info(username)
                    if user_info:
                        display_name = user_info.get('display_name', username)
                    else:
                        display_name = username
                    print(f"User {display_name} guessed: {suspect}")

# Function to start the mystery
def start_mystery():
    global game_state, suspect_count, murderer_name, last_mystery_time
    game_state = 'starting'
    suspect_count = {}
    send_message(IRC_SOCKET, "Fetching a new mystery...")
    backstory, murder, suspects, clues, murderer, reveal = fetch_mystery_from_chatgpt()

    if backstory and murder and clues and reveal and suspects and murderer:
        murderer_name = murderer.lower()
        send_message(IRC_SOCKET, f"Backstory: {backstory}")
        time.sleep(10)

        send_message(IRC_SOCKET, f"The Murder: {murder}")
        time.sleep(10)

        send_message(IRC_SOCKET, f"Suspects: {suspects}")
        time.sleep(10)

        send_message(IRC_SOCKET, f"Clue Phase: {clues}")
        time.sleep(10)

        # Include the list of suspects when asking for guesses
        send_message(IRC_SOCKET, f"Guess who the murderer is from the suspects listed! You have 60 seconds to submit your guesses.")
        game_state = 'guessing'
        # Schedule the reveal in 60 seconds
        threading.Timer(60, poll_chat_for_reveal, args=(reveal,)).start()
    else:
        send_message(IRC_SOCKET, "An error occurred fetching the mystery. Try again later.")
        game_state = None

# Function to poll the chat for guesses and reveal the murderer
def poll_chat_for_reveal(reveal):
    global suspect_count, game_state, murderer_name, last_mystery_time
    game_state = 'revealing'
    most_likely_suspect = max(suspect_count, key=suspect_count.get, default=None) if suspect_count else None

    # Before revealing, show the most guessed suspect
    if most_likely_suspect:
        send_message(IRC_SOCKET, f"Most guessed suspect: {most_likely_suspect.title()}")
        # Compare the most guessed suspect with the murderer's name
        if most_likely_suspect.lower() == murderer_name.lower():
            send_message(IRC_SOCKET, "That is correct! Let's see how it all went down...")
        else:
            send_message(IRC_SOCKET, "That is incorrect. Let's see who really did it...")
    else:
        send_message(IRC_SOCKET, "No guesses were made.")

    # Now reveal the murderer
    send_message(IRC_SOCKET, f"The Reveal: {reveal}")

    game_state = None
    last_mystery_time = time.time()

# Function to get user information using Twitch API
def get_user_info(username):
    refresh_access_token_if_needed()
    url = 'https://api.twitch.tv/helix/users'
    headers = {
        'Authorization': f'Bearer {ACCESS_TOKEN}',
        'Client-ID': TWITCH_CLIENT_ID
    }
    params = {
        'login': username
    }
    response = requests.get(url, headers=headers, params=params)
    data = response.json()
    if 'data' in data and len(data['data']) > 0:
        return data['data'][0]
    else:
        return None

# Function to handle EventSub subscriptions
def subscribe_to_eventsub():
    refresh_access_token_if_needed()
    url = 'https://api.twitch.tv/helix/eventsub/subscriptions'
    headers = {
        'Authorization': f'Bearer {ACCESS_TOKEN}',
        'Client-ID': TWITCH_CLIENT_ID,
        'Content-Type': 'application/json'
    }

    # Unsubscribe from existing subscriptions
    response = requests.get(url, headers=headers)
    data = response.json()
    if 'data' in data:
        for sub in data['data']:
            sub_id = sub['id']
            delete_url = f"{url}?id={sub_id}"
            requests.delete(delete_url, headers=headers)

    # Subscribe to channel.subscribe and channel.cheer events
    event_types = ['channel.subscribe', 'channel.cheer']
    for event_type in event_types:
        body = {
            'type': event_type,
            'version': '1',
            'condition': {
                'broadcaster_user_id': get_user_id(TWITCH_CHANNEL)
            },
            'transport': {
                'method': 'webhook',
                'callback': WEBHOOK_URL,
                'secret': TWITCH_WEBHOOK_SECRET
            }
        }
        response = requests.post(url, headers=headers, json=body)
        print(f"Subscribed to {event_type}: {response.status_code}")

# Function to get user ID from username
def get_user_id(username):
    user_info = get_user_info(username)
    if user_info:
        return user_info['id']
    else:
        return None

# Flask route to handle EventSub notifications
@app.route('/webhook', methods=['POST'])
def webhook():
    # Verify the message
    message_id = request.headers.get('Twitch-Eventsub-Message-Id')
    timestamp = request.headers.get('Twitch-Eventsub-Message-Timestamp')
    message_signature = request.headers.get('Twitch-Eventsub-Message-Signature')
    body = request.get_data().decode('utf-8')

    if not verify_signature(TWITCH_WEBHOOK_SECRET, message_id, timestamp, body, message_signature):
        print("Invalid signature.")
        return '', 403

    # Handle the message type
    message_type = request.headers.get('Twitch-Eventsub-Message-Type')

    if message_type == 'webhook_callback_verification':
        challenge = request.json['challenge']
        return challenge, 200
    elif message_type == 'notification':
        event = request.json['event']
        handle_event(event)
        return '', 200
    else:
        return '', 400

# Function to verify the signature of the EventSub message
def verify_signature(secret, message_id, timestamp, body, expected_signature):
    hmac_message = message_id + timestamp + body
    signature = hmac.new(
        secret.encode('utf-8'),
        hmac_message.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()
    computed_signature = 'sha256=' + signature
    return hmac.compare_digest(computed_signature, expected_signature)

# Function to handle EventSub events
def handle_event(event):
    global cooldown
    event_type = event['subscription']['type']
    if event_type == 'channel.subscribe':
        user_name = event['event']['user_name']
        send_message(IRC_SOCKET, f"Thank you @{user_name} for subscribing!")
        # Reduce the cooldown by 60 seconds per subscription
        cooldown_reduction = 60
        cooldown = max(60, cooldown - cooldown_reduction)
        send_message(IRC_SOCKET, f"The cooldown for the next mystery has been reduced by {cooldown_reduction} seconds!")
    elif event_type == 'channel.cheer':
        user_name = event['event']['user_name']
        bits = event['event']['bits']
        send_message(IRC_SOCKET, f"Thank you @{user_name} for cheering {bits} bits!")
        # Reduce the cooldown by 10 seconds per 100 bits
        cooldown_reduction = int(bits / 100) * 10
        if cooldown_reduction > 0:
            cooldown = max(60, cooldown - cooldown_reduction)
            send_message(IRC_SOCKET, f"The cooldown for the next mystery has been reduced by {cooldown_reduction} seconds!")

# Function to run the Flask app
def run_flask_app():
    app.run(host='0.0.0.0', port=8080)

# Main function
def main():
    try:
        connect_to_twitch()
        threading.Thread(target=receive_messages).start()
        threading.Thread(target=run_flask_app).start()
        subscribe_to_eventsub()
    except Exception as e:
        print(f"An error occurred: {e}")

if __name__ == '__main__':
    main()
