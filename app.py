from flask import Flask, render_template, Response, jsonify, request, redirect, url_for, session, send_from_directory
import cv2
import pickle
import mediapipe as mp
import numpy as np
import pyttsx3
import threading
import time
import os
import json

app = Flask(__name__)
app.secret_key = 'your_secret_key_here'  # Needed for session

# Load model
model_dict = pickle.load(open('./model/model.p', 'rb'))
model = model_dict['model']
labels_dict = {0: 'Apple', 1: 'Banana', 2: 'Potatoes', 3: 'Milk', 4: 'Bread', 5: 'Carrot', 6: 'Tomato', 7: 'Orange', 8: 'Add', 9: 'Remove'}
product_data = [
    {"name": "Apple", "image": "apple.jpeg", "price": 1.2},
    {"name": "Banana", "image": "Banana.jpeg", "price": 0.8},
    {"name": "Potatoes", "image": "Potatoes.jpeg", "price": 2.0},
    {"name": "Milk", "image": "Milk.jpeg", "price": 1.5},
    {"name": "Bread", "image": "Bread.jpeg", "price": 1.0},
    {"name": "Carrot", "image": "Carrot.jpeg", "price": 0.6},
    {"name": "Tomato", "image": "Tomato.jpeg", "price": 1.1},
    {"name": "Orange", "image": "Orange.jpeg", "price": 1.3},
]
product_labels = [p["name"] for p in product_data]
expected_features = 42

# Mediapipe setup
mp_hands = mp.solutions.hands
mp_drawing = mp.solutions.drawing_utils
mp_drawing_styles = mp.solutions.drawing_styles
hands = mp_hands.Hands(static_image_mode=False, min_detection_confidence=0.5, max_num_hands=1)

# TTS
engine = pyttsx3.init()

# State
global_state = {
    'stabilization_buffer': [],
    'stable_char': None,
    'word_buffer': "",
    'sentence': "",
    'last_registered_time': time.time(),
    'registration_delay': 2.5,
    'paused': False,
    'basket': [],  # List of products in the basket
    'pending_product': None  # Product waiting for confirmation
}

USERS_FILE = 'users.json'

def load_users():
    try:
        with open(USERS_FILE, 'r') as f:
            return json.load(f)
    except Exception:
        return []

def save_users(users):
    with open(USERS_FILE, 'w') as f:
        json.dump(users, f)

def speak_text(text):
    def tts_thread():
        engine.say(text)
        engine.runAndWait()
    threading.Thread(target=tts_thread, daemon=True).start()

def process_frame(frame):
    state = global_state
    frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    results = hands.process(frame_rgb)
    current_sign = "---"
    if results.multi_hand_landmarks:
        for hand_landmarks in results.multi_hand_landmarks:
            data_aux = []
            x_ = []
            y_ = []
            for i in range(len(hand_landmarks.landmark)):
                x = hand_landmarks.landmark[i].x
                y = hand_landmarks.landmark[i].y
                x_.append(x)
                y_.append(y)
            for i in range(len(hand_landmarks.landmark)):
                x = hand_landmarks.landmark[i].x
                y = hand_landmarks.landmark[i].y
                data_aux.append(x - min(x_))
                data_aux.append(y - min(y_))
            if len(data_aux) < expected_features:
                data_aux.extend([0]*(expected_features-len(data_aux)))
            elif len(data_aux) > expected_features:
                data_aux = data_aux[:expected_features]
            prediction = model.predict([np.asarray(data_aux)])
            predicted_character = labels_dict[int(prediction[0])]
            state['stabilization_buffer'].append(predicted_character)
            if len(state['stabilization_buffer']) > 30:
                state['stabilization_buffer'].pop(0)
            if state['stabilization_buffer'].count(predicted_character) > 25:
                current_time = time.time()
                if current_time - state['last_registered_time'] > state['registration_delay']:
                    state['stable_char'] = predicted_character
                    state['last_registered_time'] = current_time
                    current_sign = state['stable_char']
                    # Pending product logic
                    if state['stable_char'] in product_labels:
                        state['pending_product'] = state['stable_char']
                    elif state['stable_char'] == 'Add':
                        if state['pending_product']:
                            state['basket'].append(state['pending_product'])
                            state['pending_product'] = None
                    elif state['stable_char'] == 'Remove':
                        if state['basket']:
                            state['basket'].pop()
                    # (Optional) Keep word/sentence logic for TTS if needed
                    if state['stable_char'] == ' ':
                        if state['word_buffer'].strip():
                            speak_text(state['word_buffer'])
                            state['sentence'] += state['word_buffer'] + " "
                        state['word_buffer'] = ""
                    elif state['stable_char'] == '.':
                        if state['word_buffer'].strip():
                            speak_text(state['word_buffer'])
                            state['sentence'] += state['word_buffer'] + "."
                        state['word_buffer'] = ""
                    else:
                        state['word_buffer'] += state['stable_char']
            mp_drawing.draw_landmarks(
                frame,
                hand_landmarks,
                mp_hands.HAND_CONNECTIONS,
                mp_drawing_styles.get_default_hand_landmarks_style(),
                mp_drawing_styles.get_default_hand_connections_style())
    return frame, current_sign

@app.route('/')
def index():
    user = session.get('user')
    return render_template('index.html', product_data=product_data, user=user)

def gen_frames():
    cap = cv2.VideoCapture(0)
    while True:
        success, frame = cap.read()
        if not success:
            break
        if not global_state['paused']:
            frame, _ = process_frame(frame)
        ret, buffer = cv2.imencode('.jpg', frame)
        frame = buffer.tobytes()
        yield (b'--frame\r\n'
               b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')

@app.route('/video_feed')
def video_feed():
    return Response(gen_frames(), mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route('/detection')
def detection():
    state = global_state
    return jsonify({
        "pending_product": state['pending_product']
    })

@app.route('/basket')
def get_basket():
    state = global_state
    basket_items = []
    for item_name in state['basket']:
        product = next((p for p in product_data if p['name'] == item_name), None)
        if product:
            basket_items.append({
                'name': product['name'],
                'image': product['image'],
                'price': product['price']
            })
        else:
            basket_items.append({'name': item_name, 'image': '', 'price': 0})
    return jsonify({"basket": basket_items})

@app.route('/basket/clear', methods=['POST'])
def clear_basket():
    state = global_state
    state['basket'] = []
    return '', 204

@app.route('/reset', methods=['POST'])
def reset():
    state = global_state
    state['word_buffer'] = ""
    state['sentence'] = ""
    state['stable_char'] = None
    state['basket'] = []
    state['pending_product'] = None
    return '', 204

@app.route('/pause', methods=['POST'])
def pause():
    state = global_state
    state['paused'] = not state['paused']
    return jsonify({"paused": state['paused']})

@app.route('/speak', methods=['POST'])
def speak():
    state = global_state
    speak_text(state['sentence'])
    return '', 204

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']
        users = load_users()
        user = next((u for u in users if u['email'] == email and u['password'] == password), None)
        if user:
            session['user'] = {'name': user['name'], 'email': user['email']}
            return redirect(url_for('index'))
        else:
            return render_template('login.html', message='Invalid email or password.')
    return render_template('login.html', message=None)

@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        name = request.form['name']
        email = request.form['email']
        password = request.form['password']
        users = load_users()
        if any(u['email'] == email for u in users):
            return render_template('signup.html', message='Email already registered.')
        users.append({'name': name, 'email': email, 'password': password})
        save_users(users)
        session['user'] = {'name': name, 'email': email}
        return redirect(url_for('index'))
    return render_template('signup.html', message=None)

@app.route('/logout')
def logout():
    session.pop('user', None)
    return redirect(url_for('index'))

@app.before_request
def require_login():
    allowed_routes = ['login', 'signup', 'static', 'video_feed']
    if not session.get('user') and request.endpoint not in allowed_routes and not request.path.startswith('/static'):
        return redirect(url_for('login'))

if __name__ == '__main__':
    app.run(debug=True) 