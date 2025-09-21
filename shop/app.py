from dotenv import load_dotenv
load_dotenv() # This line loads variables from the .env file

import sqlite3
from flask import Flask, jsonify, request, render_template, g, session, redirect, url_for
from datetime import timedelta, datetime, timezone
from flask_socketio import SocketIO, join_room, leave_room, emit
import json
import data_manager, re, os, click
from werkzeug.utils import secure_filename
from PIL import Image


app = Flask(__name__)
socketio = SocketIO(app)

from werkzeug.security import generate_password_hash, check_password_hash
# Configuration for file uploads
IMAGE_FOLDER = os.path.join('static', 'images')
USER_IMAGE_FOLDER = os.path.join('static', 'images', 'users')
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}
app.config['IMAGE_FOLDER'] = IMAGE_FOLDER
DATABASE = 'database.db'


# --- SESSION MANAGEMENT ---
# A secret key is required for sessions to work. It should be a long, random string.
# In a production environment, this MUST be loaded from an environment variable.
SECRET_KEY = os.environ.get('FLASK_SECRET_KEY')
IS_DEBUG_MODE = os.environ.get('FLASK_DEBUG', 'False').lower() in ('true', '1', 't')

if not SECRET_KEY:
    if IS_DEBUG_MODE:
        SECRET_KEY = os.urandom(24)
        print("---")
        print("WARNING: FLASK_SECRET_KEY not set. A temporary key has been generated for this development session.")
        print("---")
    else:
        # In production, it's better to fail fast than to run with an insecure, temporary key.
        raise ValueError("CRITICAL: The FLASK_SECRET_KEY environment variable is not set in your production environment. All user sessions will be lost on server restart. Please set this variable.")

app.secret_key = SECRET_KEY
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=30)  # Set "Remember Me" duration

# --- DATABASE SETUP ---
def get_db():
    """Connect to the application's configured database. The connection is unique for each request and will be reused if this is called again."""
    if 'db' not in g:
        g.db = sqlite3.connect(DATABASE, detect_types=sqlite3.PARSE_DECLTYPES)
        g.db.row_factory = sqlite3.Row
    return g.db

@app.teardown_appcontext
def close_db(e=None):
    """If this request connected to the database, close the connection."""
    db = g.pop('db', None)
    if db is not None:
        db.close()


def init_db():
    """Clear existing data and create new tables."""
    db = get_db()
    with app.open_resource('schema.sql', mode='r') as f:
        db.cursor().executescript(f.read())
    db.commit()


@app.cli.command('init-db')
def init_db_command():
    """Creates the database tables."""
    init_db()
    click.echo('Initialized the database.')

def get_all_users():
    """Helper to read all users from the database."""
    db = get_db()
    users_rows = db.execute('SELECT * FROM users').fetchall()
    return [dict(row) for row in users_rows]

def get_user_by_id(user_id):
    """Helper to get a single user by their ID. This is much more efficient than get_all_users()."""
    if not user_id:
        return None
    db = get_db()
    user_row = db.execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone()
    return dict(user_row) if user_row else None

def get_user_by_email(email):
    """Helper to get a single user by their email."""
    if not email:
        return None
    db = get_db()
    user_row = db.execute('SELECT * FROM users WHERE email = ?', (email,)).fetchone()
    return dict(user_row) if user_row else None

@app.route('/')
def index():
    """Serves the main shop page."""
    current_user, total_unread_count = get_user_and_unread_count(session)
    
    return render_template('shop.html', user=current_user, total_unread_count=total_unread_count)

def allowed_file(filename):
    """Checks if the file's extension is allowed."""
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

@app.route('/admin')
def admin_page():
    """Serves the data management UI page."""
    user_id = session.get('user_id')
    if not user_id:
        return redirect(url_for('index', _anchor='login'))

    current_user = get_user_by_id(user_id)
    if not current_user or current_user.get('role') != 'admin':
        # If not an admin, redirect to home page.
        return redirect(url_for('index'))

    _, total_unread_count = get_user_and_unread_count(session)
    return render_template('index.html', user=current_user, total_unread_count=total_unread_count)

@app.route('/parks', methods=['GET'])
def get_parks():
    """Endpoint to get all parks, filtered by admin role."""
    user_id = session.get('user_id')
    if not user_id:
        return jsonify({"error": "Authentication required"}), 401

    current_user = get_user_by_id(user_id)
    if not current_user or current_user.get('role') != 'admin':
        return jsonify({"error": "Admin privileges required"}), 403

    all_parks = data_manager.get_all_parks()
    if current_user.get('email') == os.environ.get('MAIN_ADMIN_EMAIL'):
        return jsonify(all_parks)
    user_parks = [park for park in all_parks if park.get('admin_id') == user_id]
    return jsonify(user_parks)

@app.route('/parks', methods=['POST'])
def create_park():
    """Endpoint to create a new park with an image upload."""
    user_id = session.get('user_id')
    if not user_id:
        return jsonify({"error": "Authentication required"}), 401
    current_user = get_user_by_id(user_id)
    if not current_user or current_user.get('role') != 'admin':
        return jsonify({"error": "Admin privileges required to post products."}), 403

    park_data = request.form.to_dict()
    park_data['home_delivery'] = 'home_delivery' in request.form # This will be True or False
    required_fields = ['name', 'location', 'price', 'description', 'type']
    if not all(field in park_data for field in required_fields):
        return jsonify({"error": f"Missing one or more required fields: {required_fields}"}), 400

    # --- Handle Multiple Image Uploads ---
    # Correctly iterate through the files that were actually sent in the request.
    # This is the key fix to prevent the "mismatch" error.
    uploaded_files = [file for key, file in request.files.items() if key.startswith('image') and file.filename]
    if any(not allowed_file(f.filename) for f in uploaded_files):
        return jsonify({"error": f"Invalid file type detected. Allowed: {list(ALLOWED_EXTENSIONS)}"}), 400

    # For new products, require at least 2 images
    if len(uploaded_files) < 2:
        return jsonify({"error": "At least two images are required for a new product."}), 400

    extensions = [secure_filename(f.filename).rsplit('.', 1)[1].lower() for f in uploaded_files]
    # --- End Image Handling ---

    os.makedirs(app.config['IMAGE_FOLDER'], exist_ok=True)
    os.makedirs(USER_IMAGE_FOLDER, exist_ok=True)

    try:
        # 1. The data_manager.add_park function accepts a list of extensions
        # and returns the new park record, which includes the generated filenames.
        new_park = data_manager.add_park(park_data, extensions, user_id)
        
        if not new_park or not new_park.get('image_filenames'):
            return jsonify({"error": "Failed to create new park record."}), 500

        # 2. Save all the uploaded image files using the filenames from the created record.
        final_filenames = new_park.get('image_filenames', [])
        if len(final_filenames) != len(uploaded_files):
            # This is a failsafe. If this happens, data_manager.add_park was not updated correctly.
            # We should probably delete the DB entry here, but for now, we'll return an error.
            if not data_manager.delete_park(new_park['id']):
                print("WARNING: Could not delete the park. DB may be inconsistent.")
            return jsonify({"error": "Mismatch between uploaded files and generated filenames."}), 500

        for i, file_storage in enumerate(uploaded_files):
            image_path = os.path.join(app.config['IMAGE_FOLDER'], final_filenames[i])
            
            # Open the uploaded image file with Pillow
            img = Image.open(file_storage.stream)
            # Resize to a max of 800x800 while maintaining aspect ratio
            img.thumbnail((800, 800))
            # Save with high quality.
            img.save(image_path, quality=95, optimize=True)

        # The frontend expects the image_filenames as a JSON string, so we convert it back here for the response.
        new_park['image_filenames'] = json.dumps(new_park['image_filenames'])
        return jsonify(new_park), 201
    except Exception as e:
        # Basic error handling in case something goes wrong
        # In a production app, you might want to log this error instead of sending details to the client.
        return jsonify({"error": "An internal error occurred.", "details": str(e)}), 500

@app.route('/parks/<int:park_id>', methods=['PUT'])
def update_park_details(park_id):
    """Updates an existing park's details."""
    user_id = session.get('user_id')
    if not user_id:
        return jsonify({"error": "Authentication required"}), 401
    
    current_user = get_user_by_id(user_id)
    if not current_user or current_user.get('role') != 'admin':
        return jsonify({"error": "Admin privileges required"}), 403

    product_to_update = data_manager.get_park_by_id(park_id)
    if not product_to_update:
        return jsonify({"error": "Park not found"}), 404

    is_main_admin = current_user.get('email') == os.environ.get('MAIN_ADMIN_EMAIL')
    is_product_owner = product_to_update.get('admin_id') == user_id

    if not is_main_admin and not is_product_owner:
        return jsonify({"error": "You do not have permission to edit this product."}), 403

    update_data = request.form.to_dict()
    update_data['home_delivery'] = 'home_delivery' in request.form
    
    # --- NEW, ROBUST IMAGE HANDLING LOGIC ---
    # This logic handles adding/replacing individual images without deleting others.
    
    # Start with a copy of the existing filenames
    current_filenames_json = product_to_update.get('image_filenames', '[]')
    try:
        current_filenames = json.loads(current_filenames_json)
    except json.JSONDecodeError:
        current_filenames = []
    new_filenames = list(current_filenames) # Make a mutable copy
    files_to_save = []
    files_to_delete = []

    # Loop through the 4 possible image inputs from the form
    for i in range(4):
        file_key = f'image{i+1}'
        file = request.files.get(file_key)

        if file and file.filename:
            # A new file has been uploaded for this slot
            if not allowed_file(file.filename):
                return jsonify({"error": f"Invalid file type for {file_key}."}), 400

            # If there was an old image in this slot, mark it for deletion
            if i < len(new_filenames) and new_filenames[i]:
                files_to_delete.append(new_filenames[i])

            # Generate a new filename for the new file
            extension = secure_filename(file.filename).rsplit('.', 1)[1].lower()
            generated_filename = f"{product_to_update['id']}_{i+1}.{extension}"
            
            # Ensure the list is long enough before assignment
            while len(new_filenames) <= i:
                new_filenames.append(None)
            new_filenames[i] = generated_filename
            
            # Add the file to a list to be saved after DB update is successful
            files_to_save.append({'file_storage': file, 'filename': generated_filename})

    # Clean up any trailing None/null values from the filenames list
    while new_filenames and new_filenames[-1] is None:
        new_filenames.pop()

    # Add the final list of filenames to the data to be updated.
    update_data['image_filenames'] = new_filenames

    try:
        # We call update_park, which will handle updating the database record
        # with the new data, including the new list of image filenames.
        updated_park, _ = data_manager.update_park(park_id, update_data, None)
        if updated_park and isinstance(updated_park.get('image_filenames'), list):
            updated_park['image_filenames'] = json.dumps(updated_park['image_filenames'])

        # Now that the JSON is updated, handle the file system changes
        for old_filename in files_to_delete:
            old_image_path = os.path.join(app.config['IMAGE_FOLDER'], old_filename)
            if os.path.exists(old_image_path):
                os.remove(old_image_path)
        
        for item in files_to_save:
            image_path = os.path.join(app.config['IMAGE_FOLDER'], item['filename'])
            img = Image.open(item['file_storage'].stream)
            img.thumbnail((800, 800))
            img.save(image_path, quality=95, optimize=True)

        return jsonify(updated_park), 200
    except Exception as e:
        return jsonify({"error": "An internal error occurred during update.", "details": str(e)}), 500

@app.route('/parks/<int:park_id>', methods=['DELETE'])
def remove_park(park_id):
    """Endpoint to delete a park by its ID."""
    user_id = session.get('user_id')
    if not user_id:
        return jsonify({"error": "Authentication required"}), 401
    
    current_user = get_user_by_id(user_id)
    if not current_user or current_user.get('role') != 'admin':
        return jsonify({"error": "Admin privileges required"}), 403

    product_to_delete = data_manager.get_park_by_id(park_id)
    if not product_to_delete:
        return jsonify({"error": f"Park with id {park_id} not found."}), 404

    is_main_admin = current_user.get('email') == os.environ.get('MAIN_ADMIN_EMAIL')
    is_product_owner = product_to_delete.get('admin_id') == user_id

    if not is_main_admin and not is_product_owner:
        return jsonify({"error": "You do not have permission to delete this product."}), 403

    deleted_park = data_manager.delete_park(park_id)
    if deleted_park:
        # If images are associated, delete them from the file system
        image_filenames_json = deleted_park.get('image_filenames', '[]')
        try:
            image_filenames = json.loads(image_filenames_json)
        except json.JSONDecodeError:
            image_filenames = []
        if image_filenames:
            for filename in image_filenames:
                image_path = os.path.join(app.config['IMAGE_FOLDER'], filename)
                if os.path.exists(image_path):
                    os.remove(image_path)
                
        return jsonify({"message": f"Park with id {park_id} deleted."}), 200
    else:
        return jsonify({"error": f"Park with id {park_id} not found."}), 404

@app.route('/api/products')
def get_products():
    """
    This is the route the shop.html page fetches data from.
    It now reads directly from the products.json file, which is managed
    by the admin panel, ensuring data consistency.
    """
    products = data_manager.get_all_parks()

    # Add a compatibility field for the old admin panel JS that expects a single image_filename
    for park in products:
        filenames_json = park.get('image_filenames', '[]')
        try:
            filenames = json.loads(filenames_json)
        except (json.JSONDecodeError, TypeError):
            filenames = []
        if filenames and len(filenames) > 0:
            park['image_filename'] = filenames[0]

    # --- Pagination Logic ---
    # Get 'page' and 'limit' from the query string, with default values
    try:
        page = int(request.args.get('page', 1))
        limit = int(request.args.get('limit', 50))
    except (ValueError, TypeError):
        page = 1
        limit = 50

    start_index = (page - 1) * limit
    end_index = start_index + limit
    return jsonify(products[start_index:end_index])

@app.route('/api/search')
def search_products():
    """Endpoint for searching products by name, description, or type."""
    query = request.args.get('q', '').lower().strip()
    if not query:
        return jsonify([]) # Return empty list if query is empty

    all_products = data_manager.get_all_parks()
    query_tokens = set(query.split())
    
    product_scores = {} # Use a dict to store scores, with product ID as key

    for product in all_products:
        product_id = product.get('id')
        if not product_id:
            continue

        score = 0
        
        # Field values
        product_type = product.get('type', '').lower()
        product_name = product.get('name', '').lower()
        product_desc = product.get('description', '').lower()

        # --- Scoring Logic ---
        # 1. Category/Type: Highest priority.
        if query in product_type:
            score += 100
        
        # 2. Name: High priority.
        if query in product_name:
            score += 80
        
        name_tokens = set(product_name.split())
        name_matches = len(query_tokens.intersection(name_tokens))
        score += name_matches * 20

        # 3. Description: Keyword-based.
        desc_tokens = set(product_desc.split())
        desc_matches = len(query_tokens.intersection(desc_tokens))
        score += desc_matches * 5

        if score > 0:
            product_scores[product_id] = {'product': product, 'score': score}

    # Convert dict to list and sort by score, descending
    scored_products = sorted(list(product_scores.values()), key=lambda x: x['score'], reverse=True)
    
    # Extract just the product dictionaries for the final result
    return jsonify([item['product'] for item in scored_products])
# --- CHAT SYSTEM ---

@app.route('/api/product/<string:product_id>')
def get_product_details_api(product_id):
    """API endpoint to get details for a single product."""
    if not product_id:
        return jsonify({"error": "Product ID is required"}), 400

    product = data_manager.get_park_by_id(product_id)

    if not product:
        return jsonify({"error": "Product not found"}), 404
    
    # The client-side JS expects the image_filename for constructing the path
    return jsonify(product)

def get_user_and_unread_count(session):
    """Helper to get the current user and their total unread message count from the new schema."""
    user_id = session.get('user_id')
    if not user_id:
        return None, 0

    current_user = get_user_by_id(user_id)
    if not current_user:
        return None, 0

    db = get_db()
    query = """
        SELECT COUNT(id) FROM messages
        WHERE conversation_id IN (
            SELECT id FROM conversations WHERE (id LIKE ? OR id LIKE ?) AND ? NOT IN (SELECT value FROM json_each(deleted_by))
        ) AND sender_id != ? AND seen = 0
    """
    like_pattern_user = f"{user_id}-%"
    like_pattern_admin = f"%-{user_id}"
    
    count_row = db.execute(query, (like_pattern_user, like_pattern_admin, user_id, user_id)).fetchone()
    total_unread_count = count_row[0] if count_row else 0
            
    return current_user, total_unread_count

def get_message_from_row(row):
    """Helper to convert a message DB row to a dictionary."""
    if not row: return None
    return {
        "id": row['id'],
        "conversation_id": row['conversation_id'],
        "sender_id": row['sender_id'],
        "text": row['text'],
        "timestamp": row['timestamp'],
        "seen": bool(row['seen']),
        "is_system": bool(row['is_system'])
    }

@app.route('/chat')
def chat_page():
    """
    Acts as a router. It ensures a conversation exists between the participants
    and handles creating automated report messages before redirecting the user
    to their appropriate chat dashboard.
    """
    user_id = session.get('user_id')
    if not user_id:
        return redirect(url_for('index', _anchor='login'))

    users = get_all_users()
    current_user = next((user for user in users if user.get('id') == user_id), None)
    if not current_user:
        session.clear()
        return redirect(url_for('index', _anchor='login'))

    # Determine the target admin for the conversation
    target_admin_id = None
    main_admin = next((u for u in users if u.get('email') == os.environ.get('MAIN_ADMIN_EMAIL')), None)

    is_support_request = request.args.get('support') == 'true'
    report_merchant_id = request.args.get('report_merchant_id')
    
    if is_support_request or report_merchant_id:
        if main_admin:
            target_admin_id = main_admin['id']
    elif request.args.get('admin_id'):
        target_admin_id = request.args.get('admin_id')
    elif request.args.get('product_id'):
        product = data_manager.get_park_by_id(request.args.get('product_id'))
        if product:
            target_admin_id = product.get('admin_id')
            # Inquiry tracking
            if product.get('admin_id') != user_id:
                inquired_session_key = f'inquired_{product.get("id")}'
                if not session.get(inquired_session_key):
                    data_manager.increment_product_inquiry(product.get("id"))
                    session[inquired_session_key] = True

    # If a target is identified, ensure the conversation exists and handle reports
    if target_admin_id and target_admin_id != user_id:
        conversation_key = f"{user_id}-{target_admin_id}"
        
        db = get_db()
        # Ensure the conversation row exists.
        db.execute('INSERT OR IGNORE INTO conversations (id) VALUES (?)', (conversation_key,))

        # --- NEW: Check if the conversation is truly new (has no messages) ---
        message_count_row = db.execute('SELECT COUNT(id) FROM messages WHERE conversation_id = ?', (conversation_key,)).fetchone()
        if message_count_row[0] == 0:
            # This is a brand new conversation. Create a system message to initialize it.
            # This ensures it appears in the conversation list.
            system_text = "Conversation started."
            db.execute(
                """
                INSERT INTO messages (conversation_id, sender_id, text, timestamp, is_system)
                VALUES (?, ?, ?, ?, ?)
                """,
                (conversation_key, user_id, system_text, datetime.now(timezone.utc).isoformat(), 1)
            )

        db.commit()

        # Handle automated report message creation
        if report_merchant_id and main_admin:
            report_merchant_name = request.args.get('report_merchant_name')
            report_text = (
                f"--- AUTOMATED REPORT ---\n"
                f"User '{current_user['name']}' ({current_user['id']}) is reporting a store.\n\n"
                f"Store Name: {report_merchant_name}\n"
                f"Store ID: {report_merchant_id}"
            )
            
            # To avoid duplicating logic, we can simulate a call to the handler.
            with app.test_request_context('/'):
                handle_new_message({
                    'text': report_text,
                    'conversation_id': conversation_key,
                    'is_system': True
                }, sender_id=user_id) # Pass sender_id explicitly

    # Redirect to the correct dashboard
    if current_user.get('role') == 'admin':
        if target_admin_id and target_admin_id != user_id:
            return redirect(url_for('admin_chat_dashboard', open_convo_with_admin=target_admin_id))
        return redirect(url_for('admin_chat_dashboard'))
    else:
        if target_admin_id:
            return redirect(url_for('my_chats_page', open_convo_with_admin=target_admin_id))
        return redirect(url_for('my_chats_page'))

@app.route('/admin/chat')
def admin_chat_dashboard():
    """Serves the admin chat dashboard, showing all conversations they are part of."""
    user_id = session.get('user_id')
    if not user_id:
        return redirect(url_for('index', _anchor='login'))

    current_user = get_user_by_id(user_id)
    if not current_user or current_user.get('role') != 'admin':
        return redirect(url_for('index'))

    is_main_admin = current_user.get('email') == os.environ.get('MAIN_ADMIN_EMAIL')
    admin_id = current_user['id']

    db = get_db()
    convo_list = []

    # SQL query to get all conversations for the admin, along with the last message in each.
    query = """
        SELECT c.id, c.deleted_by, m.text, m.timestamp, m.sender_id,
               (SELECT COUNT(*) FROM messages sub_m WHERE sub_m.conversation_id = c.id AND sub_m.seen = 0 AND sub_m.sender_id != ?) as unread_count
        FROM conversations c
        JOIN messages m ON m.id = (SELECT MAX(id) FROM messages WHERE conversation_id = c.id)
        WHERE (? OR (c.id LIKE ? OR c.id LIKE ?)) AND ? NOT IN (SELECT value FROM json_each(c.deleted_by))
        ORDER BY m.timestamp DESC;
    """
    like_pattern_user = f'{admin_id}-%'
    like_pattern_admin = f'%-{admin_id}'
    
    convo_rows = db.execute(query, (admin_id, is_main_admin, like_pattern_user, like_pattern_admin, admin_id)).fetchall()

    # --- PERFORMANCE OPTIMIZATION ---
    # Collect all user IDs from the conversations, then fetch them in one query.
    user_ids_to_fetch = set()
    for row in convo_rows:
        try:
            user_part, admin_part = row['id'].split('-')
            user_ids_to_fetch.add(user_part)
            user_ids_to_fetch.add(admin_part)
        except ValueError:
            continue

    if not user_ids_to_fetch: # No conversations, no users to fetch
        user_map = {}
    else:
        placeholders = ','.join('?' for _ in user_ids_to_fetch)
        user_rows = db.execute(f"SELECT id, name, photo, role FROM users WHERE id IN ({placeholders})", tuple(user_ids_to_fetch)).fetchall()
        user_map = {dict(row)['id']: dict(row) for row in user_rows}

    for row in convo_rows:
        try:
            user_part, admin_part = row['id'].split('-')
        except ValueError:
            continue

        # Determine who the "other person" is in the chat
        other_user_id = user_part if admin_id == admin_part else (admin_part if user_id == user_part else user_part)
        other_user_info = user_map.get(other_user_id)
        if not other_user_info: continue

        convo_list.append({ "id": row['id'], "user_name": other_user_info['name'], "user_photo": other_user_info.get('photo'), "user_id": other_user_info['id'], "admin_name": user_map.get(admin_part, {}).get('name', 'N/A'), "last_message_text": re.sub(r'<[^>]+>', ' ', row['text']).strip() or '[System Message]', "last_message_time": row['timestamp'], "unread_count": row['unread_count'] })

    return render_template('admin_chat.html', user=current_user, convo_list=convo_list, is_main_admin=is_main_admin, user_map=user_map)

@app.route('/api/conversation/<string:conversation_id>/messages')
def get_conversation_messages(conversation_id):
    """API endpoint to get the message history for a conversation."""
    user_id = session.get('user_id')
    if not user_id:
        return jsonify({"error": "Not authenticated"}), 401

    # Security check: ensure the user is part of this conversation
    if user_id not in conversation_id.split('-'):
        return jsonify({"error": "Forbidden"}), 403

    db = get_db()
    # Mark messages from the other party as seen
    db.execute("""
        UPDATE messages SET seen = 1 
        WHERE conversation_id = ? AND sender_id != ? AND seen = 0
    """, (conversation_id, user_id))
    db.commit()

    # Fetch all messages for the conversation
    message_rows = db.execute('SELECT * FROM messages WHERE conversation_id = ? ORDER BY timestamp ASC', (conversation_id,)).fetchall()
    
    # Convert rows to dictionaries
    messages = [get_message_from_row(row) for row in message_rows]

    # Attach user/admin info to each message for the frontend
    try:
        user_part_id, admin_part_id = conversation_id.split('-')
        # Efficiently fetch only the two users involved.
        user_info = get_user_by_id(user_part_id)
        admin_info = get_user_by_id(admin_part_id)
    except ValueError:
        user_info = None
        admin_info = None

    for msg in messages:
        if user_info:
            msg['user_info'] = {'id': user_info['id'], 'name': user_info['name'], 'photo': user_info.get('photo')}
        if admin_info:
            msg['admin_info'] = {'id': admin_info['id'], 'name': admin_info['name'], 'photo': admin_info.get('photo')}

    return jsonify(messages)

@app.route('/api/conversation/<string:conversation_id>/delete', methods=['POST'])
def delete_conversation(conversation_id):
    user_id = session.get('user_id')
    if not user_id:
        return jsonify({"error": "Not authenticated"}), 401

    db = get_db()
    convo_row = db.execute('SELECT * FROM conversations WHERE id = ?', (conversation_id,)).fetchone()
    if not convo_row:
        return jsonify({"error": "Conversation not found"}), 404

    deleted_by = json.loads(convo_row['deleted_by'])
    if user_id not in deleted_by:
        deleted_by.append(user_id)

    try:
        user_part, admin_part = conversation_id.split('-')
        # If both participants have deleted it, remove the conversation and all its messages
        if user_part in deleted_by and admin_part in deleted_by:
            db.execute('DELETE FROM messages WHERE conversation_id = ?', (conversation_id,))
            db.execute('DELETE FROM conversations WHERE id = ?', (conversation_id,))
        else:
            # Otherwise, just update the deleted_by list
            db.execute('UPDATE conversations SET deleted_by = ? WHERE id = ?', (json.dumps(deleted_by), conversation_id))
    except ValueError:
        # Fallback for keys that don't match the pattern, just delete
        db.execute('DELETE FROM messages WHERE conversation_id = ?', (conversation_id,))
        db.execute('DELETE FROM conversations WHERE id = ?', (conversation_id,))

    db.commit()
    return jsonify({"message": "Chat hidden successfully"}), 200

@socketio.on('connect')
def handle_connect():
    """Handles a new WebSocket connection."""
    user_id = session.get('user_id')
    if not user_id:
        return # Silently ignore unauthenticated connections
    join_room(user_id)
@socketio.on('new_message')
def handle_new_message(data, sender_id=None):
    """Handles receiving a new message from a client."""
    # --- CRITICAL FIX for real-time messaging ---
    # We must manage the DB connection manually within the Socket.IO handler.
    # Relying on Flask's `g` object can cause the connection to close before `emit` is finished.
    db = None
    try:
        # If sender_id is not passed directly, get it from the session.
        if sender_id is None: sender_id = session.get('user_id')
        if not sender_id: # If still no sender, we can't proceed.
            return # Not authenticated

        text = data.get('text')
        conversation_id = data.get('conversation_id')
        is_system = data.get('is_system', False)
        temp_id = data.get('temp_id') # For optimistic UI updates

        if not text or not conversation_id:
            return # Ignore invalid messages

        # Determine target room from the conversation_id
        try:
            user_part, admin_part = conversation_id.split('-')
            target_id = admin_part if sender_id == user_part else user_part
        except ValueError:
            return # Invalid key format

        db = sqlite3.connect(DATABASE, detect_types=sqlite3.PARSE_DECLTYPES)
        db.row_factory = sqlite3.Row
        
        # Resurrect conversation if recipient had deleted it
        convo_row = db.execute('SELECT deleted_by FROM conversations WHERE id = ?', (conversation_id,)).fetchone()
        if convo_row:
            deleted_by = json.loads(convo_row['deleted_by'])
            if target_id in deleted_by:
                deleted_by.remove(target_id)
                db.execute('UPDATE conversations SET deleted_by = ? WHERE id = ?', (json.dumps(deleted_by), conversation_id))

        # Insert the new message into the new messages table
        cursor = db.cursor()
        cursor.execute(
            """
            INSERT INTO messages (conversation_id, sender_id, text, timestamp, is_system)
            VALUES (?, ?, ?, ?, ?)
            """,
            (conversation_id, sender_id, text, datetime.now(timezone.utc).isoformat(), 1 if is_system else 0)
        )
        new_message_id = cursor.lastrowid
        db.commit()

        # Fetch the full message to broadcast it
        new_message_row = db.execute('SELECT * FROM messages WHERE id = ?', (new_message_id,)).fetchone()
        message_to_emit = get_message_from_row(new_message_row)
        message_to_emit['temp_id'] = temp_id # Echo back the temp_id

        # --- PERFORMANCE OPTIMIZATION ---
        user_ids_to_fetch = {user_part, admin_part}
        main_admin_email = os.environ.get('MAIN_ADMIN_EMAIL')
        main_admin_row = db.execute('SELECT id FROM users WHERE email = ?', (main_admin_email,)).fetchone()
        main_admin_id = main_admin_row['id'] if main_admin_row else None
        if main_admin_id:
            user_ids_to_fetch.add(main_admin_id)

        placeholders = ','.join('?' for _ in user_ids_to_fetch)
        query = f"SELECT id, name, photo FROM users WHERE id IN ({placeholders})"
        user_rows = db.execute(query, tuple(user_ids_to_fetch)).fetchall()
        users_map = {dict(row)['id']: dict(row) for row in user_rows}

        user_info = users_map.get(user_part)
        admin_info = users_map.get(admin_part)

        if user_info:
            message_to_emit['user_info'] = {'id': user_info['id'], 'name': user_info['name'], 'photo': user_info.get('photo')}
        if admin_info:
            message_to_emit['admin_info'] = {'id': admin_info['id'], 'name': admin_info['name'], 'photo': admin_info.get('photo')}

        # --- Real-time message distribution ---
        emit('receive_message', message_to_emit, room=target_id)
        emit('receive_message', message_to_emit, room=sender_id)

        if main_admin_id and main_admin_id not in [sender_id, target_id]:
            emit('receive_message', message_to_emit, room=main_admin_id)

    finally:
        if db:
            db.close()


# --- User Account Creation & Login ---

@app.route('/login', methods=['POST'])
def login_user():
    """
    Endpoint to log a user in.
    Expects a JSON payload with: email and password.
    """
    data = request.get_json()
    if not data:
        return jsonify({"error": "Invalid JSON payload"}), 400

    email = data.get('email')
    password = data.get('password')
    remember_me = data.get('remember_me') # Check for the 'remember_me' flag

    if not email or not password:
        return jsonify({"error": "Email and password are required"}), 400

    db = get_db()
    user_found_row = db.execute('SELECT * FROM users WHERE email = ?', (email,)).fetchone()

    if user_found_row:
        user_found = dict(user_found_row)
        if user_found and check_password_hash(user_found.get('password'), password):
            # Store the user's ID in the session to "log them in"
            session['user_id'] = user_found.get('id')
            # If "Remember Me" was checked, make the session permanent.
            if remember_me:
                session.permanent = True

            return jsonify({"message": "Login successful", "user_id": user_found.get('id')}), 200
        else:
            return jsonify({"error": "Invalid email or password"}), 401
    return jsonify({"error": "Invalid email or password"}), 401


@app.route('/create_user', methods=['POST'])
def create_user():
    """
    Endpoint to create a new user and store it in users.json.
    Expects a JSON payload with: name, email, password, and optional fields
    like number, location, and photo.
    """
    # Get data from the POST request
    data = request.get_json()
    if not data:
        return jsonify({"error": "Invalid JSON payload"}), 400

    # Extract user details from the request
    name = data.get('name')
    email = data.get('email')
    password = data.get('password')

    # Basic validation for required fields
    if not all([name, email, password]):
        return jsonify({"error": "Missing required fields: name, email, and password are required"}), 400

    db = get_db()
    # Generate a new unique ID
    max_id_row = db.execute('SELECT MAX(CAST(id AS INTEGER)) FROM users').fetchone()
    max_id = max_id_row[0] if max_id_row and max_id_row[0] is not None else 0
    new_id = max_id + 1
    formatted_id = f"{new_id:09d}"

    new_user = {
        "id": formatted_id, "name": name, "email": email,
        "number": data.get('number'), "location": data.get('location'),
        "password": generate_password_hash(password), "photo": data.get('photo'),
        "role": "normal", "reviews": "[]"
    }

    try:
        db.execute(
            """
            INSERT INTO users (id, name, email, number, location, password, photo, role, reviews)
            VALUES (:id, :name, :email, :number, :location, :password, :photo, :role, :reviews)
            """,
            new_user
        )
        db.commit()
    except sqlite3.IntegrityError:
        return jsonify({"error": "Email address already in use."}), 409

    return jsonify({"message": "User created successfully", "user": new_user}), 201

@app.route('/logout')
def logout():
    """Logs the user out by clearing the session."""
    session.clear()
    return redirect(url_for('index'))

@app.route('/update_profile', methods=['POST'])
def update_profile():
    """Endpoint for users to update their own profile."""
    user_id = session.get('user_id')
    if not user_id:
        return jsonify({"error": "Not authenticated"}), 401

    db = get_db()
    user_row = db.execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone()
    if not user_row:
        return jsonify({"error": "User not found"}), 404

    current_user = dict(user_row)
    
    # Update text fields
    update_data = {
        'id': user_id,
        'name': request.form.get('name', current_user['name']),
        'number': request.form.get('number', current_user.get('number')),
        'photo': current_user.get('photo') # Default to old photo
    }

    # Handle photo upload
    photo = request.files.get('photo')
    if photo and photo.filename != '':
        if not allowed_file(photo.filename):
            return jsonify({"error": "Invalid file type for photo"}), 400
        
        os.makedirs(USER_IMAGE_FOLDER, exist_ok=True)
        
        # Create a unique filename based on user ID to prevent conflicts
        extension = secure_filename(photo.filename).rsplit('.', 1)[1].lower()
        new_filename = f"user_{user_id}.{extension}"
        new_filepath = os.path.join(USER_IMAGE_FOLDER, new_filename)

        # Delete old photo if it exists and has a different name
        old_photo_path = current_user.get('photo')
        if old_photo_path and old_photo_path != f"/{new_filepath.replace(os.path.sep, '/')}":
            # Construct full path from web path
            old_fs_path = os.path.join('static', *old_photo_path.strip('/').split('/')[1:])
            if os.path.exists(old_fs_path):
                os.remove(old_fs_path)

        # Save the new photo
        img = Image.open(photo.stream)
        img.thumbnail((200, 200)) # Resize profile pictures
        img.save(new_filepath, quality=90)

        # Store the web-accessible path
        update_data['photo'] = f"/{USER_IMAGE_FOLDER}/{new_filename}".replace(os.path.sep, '/')

    # Save the updated user list
    db.execute('UPDATE users SET name = :name, number = :number, photo = :photo WHERE id = :id', update_data)
    db.commit()

    # Fetch the fully updated user to return
    updated_user_row = db.execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone()
    current_user = dict(updated_user_row)
    return jsonify({"message": "Profile updated successfully", "user": current_user}), 200

@app.route('/apply-merchant')
def apply_merchant_page():
    """Serves the page for a normal user to apply to become a merchant."""
    user_id = session.get('user_id')
    if not user_id:
        return redirect(url_for('index', _anchor='login'))

    current_user, total_unread_count = get_user_and_unread_count(session)
    if not current_user:
        session.clear()
        return redirect(url_for('index', _anchor='login'))
    
    # If user is already an admin, redirect them to their product manager
    if current_user.get('role') == 'admin':
        return redirect(url_for('admin_page'))

    return render_template('merchant_application.html', user=current_user, total_unread_count=total_unread_count)

@app.route('/api/apply-merchant', methods=['POST'])
def process_merchant_application():
    """Processes the merchant application form and upgrades the user's role."""
    user_id = session.get('user_id')
    if not user_id:
        return jsonify({"error": "Not authenticated"}), 401

    db = get_db()
    user_to_upgrade_row = db.execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone()

    if not user_to_upgrade_row:
        return jsonify({"error": "User not found"}), 404

    user_to_upgrade = dict(user_to_upgrade_row)
    if user_to_upgrade.get('role') != 'normal':
        return jsonify({"error": "User is already a merchant or has a different role."}), 400

    update_data = {
        'id': user_id,
        'name': request.form.get('store_name', user_to_upgrade['name']),
        'number': request.form.get('phone_number', user_to_upgrade.get('number')),
        'photo': user_to_upgrade.get('photo'),
        'location': user_to_upgrade.get('location')
    }

    ip_city = request.form.get('ip_city')
    map_address = request.form.get('map_address')
    if map_address:
        update_data['location'] = map_address
    elif ip_city:
        update_data['location'] = ip_city

    photo = request.files.get('photo')
    if photo and photo.filename != '':
        if not allowed_file(photo.filename):
            return jsonify({"error": "Invalid file type for photo"}), 400
        os.makedirs(USER_IMAGE_FOLDER, exist_ok=True)
        extension = secure_filename(photo.filename).rsplit('.', 1)[1].lower()
        new_filename = f"user_{user_id}.{extension}"
        new_filepath = os.path.join(USER_IMAGE_FOLDER, new_filename)
        img = Image.open(photo.stream)
        img.thumbnail((200, 200))
        img.save(new_filepath, quality=90)
        update_data['photo'] = f"/{USER_IMAGE_FOLDER}/{new_filename}".replace(os.path.sep, '/')

    db.execute("""
        UPDATE users SET name = :name, number = :number, location = :location, photo = :photo,
        role = 'admin', ratings_total = 5, ratings_count = 1, reviews = '[]'
        WHERE id = :id
    """, update_data)
    db.commit()

    user_to_upgrade = dict(db.execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone())
    return jsonify({"message": "Congratulations! You are now a merchant.", "user": user_to_upgrade}), 200

@app.route('/api/product-page/<string:product_id>')
def get_product_page_data(product_id):
    """API endpoint to fetch all data needed for the product detail page."""
    if not product_id:
        return jsonify({"error": "Product ID is missing."}), 400

    product = data_manager.get_park_by_id(product_id)
    if not product:
        return jsonify({"error": "Product not found."}), 404

    # Increment view count
    data_manager.increment_product_view(product_id)

    # Get admin/merchant info
    product_admin = get_user_by_id(product.get('admin_id'))
    admin_data = None
    if product_admin:
        admin_data = {
            'id': product_admin.get('id'),
            'name': product_admin.get('name'),
            'photo': product_admin.get('photo'),
            'number': product_admin.get('number'),
            'avg_rating': round(product_admin.get('ratings_total', 0) / product_admin.get('ratings_count', 1), 2),
            'ratings_count': product_admin.get('ratings_count', 1)
        }

    # Get stats
    views_count = product.get('views', 0) + 1
    contacts_count = product.get('inquiries', 0)

    # Format image paths
    image_filenames_json = product.get('image_filenames', '[]')
    try:
        image_filenames = json.loads(image_filenames_json)
    except json.JSONDecodeError:
        image_filenames = []
    product['images'] = [f"/static/images/{fname}" for fname in image_filenames if fname]

    return jsonify({
        "product": product,
        "admin": admin_data,
        "views": views_count,
        "contacts": contacts_count
    })

@app.route('/api/products/similar/<string:product_id>')
def get_similar_products(product_id):
    """API endpoint to get products similar to the given one."""
    if not product_id:
        return jsonify({"error": "Product ID is required"}), 400

    all_products = data_manager.get_all_parks()
    current_product = next((p for p in all_products if p.get('id') == product_id), None)

    if not current_product:
        return jsonify({"error": "Product not found"}), 404

    current_category = current_product.get('type')
    if not current_category:
        return jsonify([]), 200 # No category, so no similar items

    # Find similar products (same category, not the same product), limit to 10
    similar_products = [
        p for p in all_products 
        if p.get('type') == current_category and p.get('id') != product_id
    ][:10]

    return jsonify(similar_products)

@app.route('/api/store/<string:merchant_id>/review', methods=['POST'])
def review_store(merchant_id):
    user_id = session.get('user_id')
    if not user_id:
        return jsonify({"error": "You must be logged in to leave a review."}), 401

    data = request.get_json()
    rating = data.get('rating')
    if not rating or not 1 <= rating <= 5:
        return jsonify({"error": "A valid rating between 1 and 5 is required."}), 400

    db = get_db()
    merchant_row = db.execute('SELECT * FROM users WHERE id = ? AND role = ?', (merchant_id, 'admin')).fetchone()
    
    if not merchant_row:
        return jsonify({"error": "Merchant not found."}), 404

    if user_id == merchant_id:
        return jsonify({"error": "You cannot review your own store."}), 403

    merchant = dict(merchant_row)
    
    new_ratings_total = merchant.get('ratings_total', 0) + rating
    new_ratings_count = merchant.get('ratings_count', 0) + 1
    
    comment = data.get('comment')
    reviews_json = merchant.get('reviews', '[]')
    try:
        reviews = json.loads(reviews_json)
    except json.JSONDecodeError:
        reviews = []

    if comment:
        reviews.append({
            "user_id": user_id, "comment": comment, "rating": rating,
            "timestamp": datetime.now(timezone.utc).isoformat()
        })

    db.execute('UPDATE users SET ratings_total = ?, ratings_count = ?, reviews = ? WHERE id = ?',
               (new_ratings_total, new_ratings_count, json.dumps(reviews), merchant_id))
    db.commit()

    new_avg_rating = round(merchant['ratings_total'] / merchant['ratings_count'], 2)
    return jsonify({"message": "Review submitted successfully!", "new_avg_rating": new_avg_rating, "new_ratings_count": merchant['ratings_count']}), 200

@app.route('/product')
def product_page():
    """Serves the product detail page shell. JS will fetch the data."""
    current_user, total_unread_count = get_user_and_unread_count(session)
    return render_template('product.html', user=current_user, total_unread_count=total_unread_count)

@app.route('/store')
def store_page():
    """Serves the merchant's store page."""
    merchant_id = request.args.get('id')
    if not merchant_id:
        # In a real app, maybe render an error template
        return "Merchant ID is required.", 400
    
    merchant = get_user_by_id(merchant_id)
    
    if not merchant:
        return "Merchant not found.", 404

    all_products = data_manager.get_all_parks()
    
    # Add the first image filename for easier access in the template
    for product in all_products:
        filenames_json = product.get('image_filenames', '[]')
        try:
            filenames = json.loads(filenames_json)
        except json.JSONDecodeError:
            filenames = []
        if filenames and len(filenames) > 0:
            product['image_filename'] = filenames[0]
        else:
            product['image_filename'] = None # Ensure the key exists

    merchant_products = [p for p in all_products if p.get('admin_id') == merchant_id]
    
    # Sort products, newest first, for a consistent store view
    merchant_products.sort(key=lambda x: x.get('date_added', ''), reverse=True)

    ratings_total = merchant.get('ratings_total', 0)
    ratings_count = merchant.get('ratings_count', 0)
    avg_rating = round(ratings_total / ratings_count, 2) if ratings_count > 0 else 0

    current_user, total_unread_count = get_user_and_unread_count(session)
    return render_template('store.html', user=current_user, total_unread_count=total_unread_count, merchant=merchant, products=merchant_products, avg_rating=avg_rating, ratings_count=ratings_count)

@app.route('/my-chats')
def my_chats_page():
    """Serves a page for the user to see all their conversations."""
    user_id = session.get('user_id')
    if not user_id:
        return redirect(url_for('index', _anchor='login'))

    current_user = get_user_by_id(user_id)
    if not current_user:
        session.clear()
        return redirect(url_for('index', _anchor='login'))
    
    if current_user.get('role') == 'admin':
        return redirect(url_for('admin_chat_dashboard')) # Admins go to their dashboard

    user_convos_list = []
    db = get_db()

    query = """
        SELECT c.id, c.deleted_by, m.text, m.timestamp, m.sender_id,
               (SELECT COUNT(*) FROM messages sub_m WHERE sub_m.conversation_id = c.id AND sub_m.seen = 0 AND sub_m.sender_id != ?) as unread_count
        FROM conversations c
        JOIN messages m ON m.id = (SELECT MAX(id) FROM messages WHERE conversation_id = c.id)
        WHERE c.id LIKE ? AND ? NOT IN (SELECT value FROM json_each(c.deleted_by))
        ORDER BY m.timestamp DESC;
    """
    convo_rows = db.execute(query, (user_id, f"{user_id}-%", user_id)).fetchall()

    # --- PERFORMANCE OPTIMIZATION ---
    # Collect all admin IDs from the conversations, then fetch them in one query.
    admin_ids_to_fetch = set()
    for row in convo_rows:
        try:
            _, admin_part_id = row['id'].split('-')
            admin_ids_to_fetch.add(admin_part_id)
        except ValueError:
            continue

    if not admin_ids_to_fetch:
        user_map = {}
    else:
        placeholders = ','.join('?' for _ in admin_ids_to_fetch)
        user_rows = db.execute(f"SELECT id, name, photo FROM users WHERE id IN ({placeholders})", tuple(admin_ids_to_fetch)).fetchall()
        user_map = {dict(row)['id']: dict(row) for row in user_rows}

    for row in convo_rows:
        _, admin_part_id = row['id'].split('-')
        admin_info = user_map.get(admin_part_id, {})
        if not admin_info:
            continue

        user_convos_list.append({
            "id": row['id'],
            "admin_id": admin_info['id'],
            "admin_name": admin_info['name'],
            "admin_photo": admin_info.get('photo'),
            "last_message_text": re.sub(r'<[^>]+>', ' ', row['text']).strip() or '[System Message]',
            "last_message_time": row['timestamp'],
            "unread_count": row['unread_count']
        })

    _, total_unread_count = get_user_and_unread_count(session)
    return render_template('my_chats.html', user=current_user, total_unread_count=total_unread_count, convo_list=user_convos_list)

@app.route('/saved')
def saved_items_page():
    """Serves the saved items page."""
    current_user, total_unread_count = get_user_and_unread_count(session)
    if not current_user:
        return redirect(url_for('index', _anchor='login'))
    return render_template('saved_items.html', user=current_user, total_unread_count=total_unread_count)

@app.route('/account')
def account_page():
    """Serves the user account/profile editing page."""
    user_id = session.get('user_id')
    if not user_id:
        return redirect(url_for('index', _anchor='login'))

    current_user, total_unread_count = get_user_and_unread_count(session)
    if not current_user:
        session.clear()
        return redirect(url_for('index', _anchor='login'))
    
    return render_template('account.html', user=current_user, total_unread_count=total_unread_count)

@app.route('/settings')
def settings_page():
    """Serves the application settings page (e.g., theme)."""
    current_user, total_unread_count = get_user_and_unread_count(session)
    # The page is now accessible to non-logged-in users.
    # The template handles conditional display of user-specific content.
    return render_template('settings.html', user=current_user, total_unread_count=total_unread_count)

if __name__ == '__main__':
    # --- Server Configuration ---
    # Set to True for development (enables auto-reload and detailed errors).
    # For production, this should be False. You can set an environment variable
    # on your server to control this. For local dev, you can set it to True.
    
    # With eventlet installed, socketio.run() will automatically use it as a
    # production-ready server when debug is False. 
    # For development, we let Socket.IO handle the reloader to avoid conflicts.
    # For production (`IS_DEBUG_MODE = False`), it will run without the reloader.
    print(f"--- Starting server in {'DEBUG' if IS_DEBUG_MODE else 'PRODUCTION'} mode on http://0.0.0.0:5001 ---")
    socketio.run(app, host='0.0.0.0', port=5001, use_reloader=IS_DEBUG_MODE)