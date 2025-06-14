from flask import Flask, render_template, request, redirect, url_for, session, flash
from telethon import TelegramClient, events
import os
import threading

app = Flask(__name__)
app.secret_key = 'supersecretkey'  # Change this!

API_ID = 28464245  # Replace with your API ID
API_HASH = '6fe23ca19e7c7870dc2aff57fb05c4d9'  # Replace with your API hash

SESSION_DIR = 'sessions'
if not os.path.exists(SESSION_DIR):
    os.makedirs(SESSION_DIR)

clients = {}  # Active clients per session


def start_forwarding(phone, source, target):
    session_path = os.path.join(SESSION_DIR, phone)
    client = TelegramClient(session_path, API_ID, API_HASH)

    @client.on(events.NewMessage(chats=source))
    async def handler(event):
        await client.send_message(target, event.message)

    client.start()
    clients[phone] = client
    print(f'🔁 Forwarding started for {phone}')
    client.run_until_disconnected()


@app.route('/', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        phone = request.form['phone']
        session['phone'] = phone
        client = TelegramClient(os.path.join(SESSION_DIR, phone), API_ID, API_HASH)
        client.connect()
        if not client.is_user_authorized():
            client.send_code_request(phone)
        client.disconnect()
        return redirect(url_for('verify'))
    return render_template('login.html')


@app.route('/verify', methods=['GET', 'POST'])
def verify():
    phone = session.get('phone')
    if not phone:
        return redirect(url_for('login'))

    if request.method == 'POST':
        code = request.form['code']
        client = TelegramClient(os.path.join(SESSION_DIR, phone), API_ID, API_HASH)
        client.connect()
        if not client.is_user_authorized():
            client.sign_in(phone, code)
        client.disconnect()
        return redirect(url_for('select_groups'))
    return render_template('otp.html')


@app.route('/groups', methods=['GET', 'POST'])
def select_groups():
    phone = session.get('phone')
    if not phone:
        return redirect(url_for('login'))

    if request.method == 'POST':
        source = request.form['source']
        target = request.form['target']
        session['source'] = source
        session['target'] = target

        # Start forwarding in background
        threading.Thread(target=start_forwarding, args=(phone, source, target), daemon=True).start()

        return f'✅ Forwarding from {source} to {target} started for {phone}!'
    return render_template('groups.html')


if __name__ == '__main__':
    app.run(debug=True)
