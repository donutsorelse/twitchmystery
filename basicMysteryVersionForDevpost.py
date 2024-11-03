import socket
import time
import re
import requests
import threading

# Twitch and OpenAI configuration
TWITCH_BOT_USERNAME = "YourBotUsername"
OAUTH_KEY = "oauth:your_oauth_token"
TWITCH_IRC_SERVER = "irc.chat.twitch.tv"
TWITCH_IRC_PORT = 6667
TWITCH_CLIENT_ID = 'your_twitch_client_id'
TWITCH_LOGIN = 'your_twitch_login'
API_KEY = 'your_openai_api_key'
TWITCH_CHANNEL = 'your_channel_name' 

# Game state variables
game_state = None
suspect_count = {}
murderer_name = ''  # Store the murderer's name for comparison

def send_message(sock, message):
    """Send a message to the Twitch chat with clean formatting."""
    max_length = 490  # Twitch's limit is 500; we use 490 to be safe.
    lines = message.split("\n")
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
                        # No space found, split at max_length
                        split_index = max_length
                    send_line = line[:split_index]
                    line = line[split_index:].lstrip()
                print(f"Sending message to chat: {send_line}")
                sock.send(f"PRIVMSG #{TWITCH_CHANNEL} :{send_line}\r\n".encode("utf-8"))
                time.sleep(2)  # Delay to avoid rate limits

def connect_to_twitch():
    """Connect to the Twitch IRC server and authenticate the bot."""
    try:
        sock = socket.socket()
        sock.connect((TWITCH_IRC_SERVER, TWITCH_IRC_PORT))
        sock.send(f"PASS {OAUTH_KEY}\r\n".encode("utf-8"))
        sock.send(f"NICK {TWITCH_BOT_USERNAME}\r\n".encode("utf-8"))
        sock.send(f"JOIN #{TWITCH_CHANNEL}\r\n".encode("utf-8"))
        print("Connected to Twitch IRC successfully.")
        return sock
    except Exception as e:
        print(f"Error connecting to Twitch: {e}")
        return None

def fetch_mystery_from_chatgpt():
    """Fetch a new mystery using ChatGPT API."""
    headers = {
        'Content-Type': 'application/json',
        'Authorization': f'Bearer {API_KEY}'
    }
    data = {
        "model": "gpt-4",
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

def parse_mystery_response(mystery_text):
    """Parse the mystery text into sections (backstory, murder, suspects, clues, murderer, reveal)."""
    try:
        print("Parsing mystery response.")
        # Split the text into sections based on exact labels
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

def receive_messages(sock):
    """Receive messages from the Twitch chat."""
    global game_state, suspect_count
    while True:
        try:
            response = sock.recv(2048).decode("utf-8")
            if response.startswith("PING"):
                sock.send("PONG\n".encode("utf-8"))
            else:
                for line in response.strip().split("\r\n"):
                    if "PRIVMSG" in line:
                        match = re.search(r":(\w+)!.*PRIVMSG #\w+ :(.*)", line)
                        if match:
                            username = match.group(1)
                            message = match.group(2)
                            print(f"Message received from {username}: {message}")

                            if message.lower() == "!mystery":
                                if game_state is None:
                                    threading.Thread(target=start_mystery, args=(sock,)).start()
                                else:
                                    send_message(sock, "A mystery is already in progress.")
                            elif game_state == 'guessing':
                                suspect = message.strip().lower()
                                if suspect:
                                    suspect_count[suspect] = suspect_count.get(suspect, 0) + 1
        except Exception as e:
            print(f"Error receiving messages: {e}")
            break

def start_mystery(sock):
    """Start a new mystery and send it to the chat with clean formatting."""
    global game_state, suspect_count, murderer_name
    game_state = 'starting'
    suspect_count = {}
    send_message(sock, "Fetching a new mystery...")
    backstory, murder, suspects, clues, murderer, reveal = fetch_mystery_from_chatgpt()

    if backstory and murder and clues and reveal and suspects and murderer:
        murderer_name = murderer.lower()
        send_message(sock, f"Backstory: {backstory}")
        time.sleep(10)

        send_message(sock, f"The Murder: {murder}")
        time.sleep(10)

        send_message(sock, f"Suspects: {suspects}")
        time.sleep(10)

        send_message(sock, f"Clue Phase: {clues}")
        time.sleep(10)

        # Include the list of suspects when asking for guesses
        send_message(sock, f"Guess who the murderer is from the suspects listed! You have 60 seconds to submit your guesses.")
        game_state = 'guessing'
        # Schedule the reveal in 60 seconds
        threading.Timer(60, poll_chat_for_reveal, args=(sock, reveal)).start()
    else:
        send_message(sock, "An error occurred fetching the mystery. Try again later.")
        game_state = None

def poll_chat_for_reveal(sock, reveal):
    """Poll the chat for guesses and reveal the murderer."""
    global suspect_count, game_state, murderer_name
    game_state = 'revealing'
    most_likely_suspect = max(suspect_count, key=suspect_count.get, default=None) if suspect_count else None

    # Before revealing, show the most guessed suspect
    if most_likely_suspect:
        send_message(sock, f"Most guessed suspect: {most_likely_suspect.title()}")
        # Compare the most guessed suspect with the murderer's name
        if most_likely_suspect.lower() == murderer_name.lower():
            send_message(sock, "That is correct! Let's see how it all went down...")
        else:
            send_message(sock, "That is incorrect. Let's see who really did it...")
    else:
        send_message(sock, "No guesses were made.")

    # Now reveal the murderer
    send_message(sock, f"The Reveal: {reveal}")

    game_state = None

# Main program execution
def main():
    try:
        sock = connect_to_twitch()
        if sock:
            receive_messages(sock)
    except Exception as e:
        print(f"An error occurred: {e}")

if __name__ == "__main__":
    main()
