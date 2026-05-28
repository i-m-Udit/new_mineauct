import os
import sqlite3
from flask import Flask, render_template, request, session, redirect, url_for
from flask_socketio import SocketIO, emit

app = Flask(__name__)
app.secret_key = 'super_secret_mineauct_key'
# Eventlet is required for production-grade async WebSocket handling on Render
# Change this line:
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='gevent')
DB_PATH = 'mineauct.db'

# --- GAME DATA ---
HOSTILE = [
    (1, 'Warden', 'S', 1), (2, 'Wither', 'S', 2), (3, 'Ender Dragon', 'S', 3), (4, 'Ravager', 'S', 4), (5, 'Elder Guardian', 'S', 5), (6, 'Piglin Brute', 'S', 6), (7, 'Vindicator', 'S', 7),
    (8, 'Charged Creeper', 'A', 1), (9, 'Evoker', 'A', 2), (10, 'Creeper', 'A', 3), (11, 'Creaking', 'A', 4), (12, 'Hoglin', 'A', 5), (13, 'Drowned', 'A', 6), (14, 'Zoglin', 'A', 7), (15, 'Wither Skeleton', 'A', 8), (16, 'Vex', 'A', 9), (17, 'Ghast', 'A', 10), (18, 'Guardian', 'A', 11), (19, 'Witch', 'A', 12), (20, 'Shulker', 'A', 13),
    (21, 'Blaze', 'B', 1), (22, 'Phantom', 'B', 2), (23, 'Pillager', 'B', 3), (24, 'Breeze', 'B', 4), (25, 'Bogged', 'B', 5), (26, 'Parched', 'B', 5), (27, 'Stray', 'B', 5), (28, 'Magma Cube', 'B', 6),
    (29, 'Silverfish', 'F', 1), (30, 'Husk', 'F', 2), (31, 'Zombie', 'F', 3), (32, 'Slime', 'F', 4), (33, 'Endermite', 'F', 5)
]
NEUTRAL = [(101, 'Iron Golem', 'N/A', 1), (102, 'Enderman', 'N/A', 2), (103, 'Piglin', 'N/A', 3), (104, 'Zombified Piglin', 'N/A', 4), (105, 'Cave Spider', 'N/A', 5), (106, 'Spider', 'N/A', 6), (107, 'Dolphin', 'N/A', 7), (108, 'Wolf', 'N/A', 8), (109, 'Llama', 'N/A', 9), (110, 'Bee', 'N/A', 10), (111, 'Polar Bear', 'N/A', 11), (112, 'Pufferfish', 'N/A', 12)]
PASSIVE = [(201, 'Villager', 'N/A', 1), (202, 'Happy Ghast', 'N/A', 2), (203, 'Copper Golem', 'N/A', 3), (204, 'Wandering Trader', 'N/A', 4), (205, 'Snow Golem', 'N/A', 5), (206, 'Skeleton Horse', 'N/A', 6), (207, 'Brown Mooshroom', 'N/A', 7), (208, 'Mooshroom', 'N/A', 8)]

MOB_DICT = {}
for group, cat in [(HOSTILE, 'hostile'), (NEUTRAL, 'neutral'), (PASSIVE, 'passive')]:
    for m in group:
        MOB_DICT[str(m[0])] = {'id': str(m[0]), 'name': m[1], 'tier': m[2], 'rank': m[3], 'category': cat}

TIER_VAL = {'S': 4, 'A': 3, 'B': 2, 'F': 1, 'N/A': 0}
MAX_SLOTS = {'hostile': 7, 'neutral': 3, 'passive': 2}

# --- DATABASE INIT ---
def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute('CREATE TABLE IF NOT EXISTS users (username TEXT PRIMARY KEY, password TEXT, is_host INTEGER)')
    # Pre-seed the 4 players
    players = [('Donald Trump', 'donald_duck', 1), ('Vladimir Putin', 'pass2', 0), ('Xi Jinping', 'pass3', 0), ('Narendra Modi', 'pass4', 0)]
    for p in players:
        try: conn.execute('INSERT INTO users VALUES (?,?,?)', p)
        except: pass
    conn.commit()
    conn.close()

# --- GAME STATE ---
game = {
    'status': 'lobby', # lobby, auction, lineup, results
    'players': {}, # username -> {purse, purchases, lineup, is_ready}
    'queue': [],
    'current_mob_idx': 0,
    'bid': 0.5,
    'highest_bidder': None,
    'players_out': [],
    'chat': [],
    'results': {}
}

def reset_game():
    game['status'] = 'lobby'
    game['queue'] = [MOB_DICT[str(m[0])] for m in HOSTILE[3:]] + [MOB_DICT[str(m[0])] for m in NEUTRAL] + [MOB_DICT[str(m[0])] for m in PASSIVE]
    game['current_mob_idx'] = 0
    game['bid'] = 0.5
    game['highest_bidder'] = None
    game['players_out'] = []
    game['chat'] = []
    game['results'] = {}
    for p in game['players'].values():
        p.update({'purse': 1000.0, 'purchases': [], 'lineup': {'hostile':{}, 'neutral':{}, 'passive':{}}, 'is_ready': False})

def check_auction_over():
    if game['current_mob_idx'] >= len(game['queue']):
        game['status'] = 'lineup'
        socketio.emit('sync_state', game)

def resolve_match():
    scores = {p: 0 for p in game['players']}
    slot_winners = {}

    for cat, max_s in MAX_SLOTS.items():
        slot_winners[cat] = {}
        for s in range(1, max_s + 1):
            slot = str(s)
            best_player, best_val = None, None
            
            # Find the winner for this specific slot
            for p_name, p_data in game['players'].items():
                mob_id = p_data['lineup'][cat].get(slot)
                if not mob_id: continue
                
                mob = MOB_DICT[mob_id]
                # Value tuple: (Tier Value, -Rank) so higher tier wins, lower rank number wins
                val = (TIER_VAL[mob['tier']], -mob['rank']) 
                
                if best_val is None or val > best_val:
                    best_val = val
                    best_player = p_name
            
            slot_winners[cat][slot] = best_player
            if best_player:
                scores[best_player] += 1
                
    game['results'] = {'scores': scores, 'winners': slot_winners}
    game['status'] = 'results'

# --- ROUTES ---
@app.route('/')
def index():
    if 'username' not in session: return render_template('index.html', view='login')
    return render_template('index.html', view='game', username=session['username'], is_host=session.get('is_host'))

@app.route('/login', methods=['POST'])
def login():
    data = request.json
    conn = sqlite3.connect(DB_PATH)
    user = conn.execute('SELECT * FROM users WHERE username=? AND password=?', (data['username'], data['password'])).fetchone()
    if user:
        session['username'], session['is_host'] = user[0], user[2]
        if user[0] not in game['players']:
            game['players'][user[0]] = {'purse': 1000.0, 'purchases': [], 'lineup': {'hostile':{}, 'neutral':{}, 'passive':{}}, 'is_ready': False}
        return {'success': True}
    return {'success': False, 'error': 'Invalid credentials'}

@app.route('/logout')
def logout():
    session.clear()
    return redirect('/')

# --- SOCKET EVENTS ---
@socketio.on('connect')
def handle_connect():
    emit('sync_state', game)

@socketio.on('chat_msg')
def handle_chat(msg):
    if 'username' in session:
        game['chat'].append({'user': session['username'], 'text': msg})
        if len(game['chat']) > 50: game['chat'].pop(0)
        emit('sync_state', game, broadcast=True)

@socketio.on('start_game')
def start_game():
    if session.get('is_host'):
        reset_game()
        game['status'] = 'auction'
        emit('sync_state', game, broadcast=True)

@socketio.on('place_bid')
def place_bid(amount):
    user = session['username']
    p = game['players'][user]
    new_bid = game['bid'] + amount
    
    if new_bid <= p['purse'] and user not in game['players_out']:
        game['bid'] = new_bid
        game['highest_bidder'] = user
        emit('sync_state', game, broadcast=True)

@socketio.on('pass_bid')
def pass_bid():
    user = session['username']
    if user != game['highest_bidder'] and user not in game['players_out']:
        game['players_out'].append(user)
        
        active = [p for p in game['players'] if p not in game['players_out']]
        if len(active) == 0 or (len(active) == 1 and active[0] == game['highest_bidder']):
            # Auction ends for this mob
            if game['highest_bidder']:
                winner = game['highest_bidder']
                mob = game['queue'][game['current_mob_idx']]
                game['players'][winner]['purse'] -= game['bid']
                game['players'][winner]['purchases'].append(mob)
            
            game['current_mob_idx'] += 1
            game['bid'] = 0.5
            game['highest_bidder'] = None
            game['players_out'] = []
            check_auction_over()
            
        emit('sync_state', game, broadcast=True)

@socketio.on('submit_lineup')
def submit_lineup(lineup):
    user = session['username']
    game['players'][user]['lineup'] = lineup
    game['players'][user]['is_ready'] = True
    
    if all(p['is_ready'] for p in game['players'].values()):
        resolve_match()
        
    emit('sync_state', game, broadcast=True)

if __name__ == '__main__':
    init_db()
    socketio.run(app, debug=True, port=5000)
