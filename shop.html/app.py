import sqlite3
from flask import Flask, jsonify, request, render_template, g, session, redirect, url_for, send_from_directory
from datetime import timedelta, datetime, timezone
from flask_socketio import SocketIO, join_room, leave_room, emit
import json
import data_manager, re, os
import os
from werkzeug.utils import secure_filename
from PIL import Image
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)

# --- Socket.IO Configuration for Production ---
# Use Redis as the message queue if the REDIS_URL is available (on Render).
# This is CRITICAL for the chat system to work with multiple server processes.
redis_url = os.environ.get('REDIS_URL')
socketio = SocketIO(app, message_queue=redis_url)

# --- Persistent Data Configuration for Render ---
# Use environment variable for the persistent disk path, default for local dev.
PERSISTENT_STORAGE_PATH = os.environ.get('RENDER_DISK_PATH', 'persistent_data')

# --- Filesystem Paths for Uploads (where files are saved) ---
IMAGE_UPLOADS_ROOT = os.path.join(PERSISTENT_STORAGE_PATH, 'images')
ADS_UPLOADS_DIR = os.path.join(IMAGE_UPLOADS_ROOT, 'ads')
USER_UPLOADS_DIR = os.path.join(IMAGE_UPLOADS_ROOT, 'users')
app.config['IMAGE_UPLOADS_ROOT'] = IMAGE_UPLOADS_ROOT
app.config['ADS_UPLOADS_DIR'] = ADS_UPLOADS_DIR

# --- JSON Database File Paths ---
USERS_FILE = os.path.join(PERSISTENT_STORAGE_PATH, 'users.json')
ADS_FILE = os.path.join(PERSISTENT_STORAGE_PATH, 'ads.json')
CONVERSATIONS_FILE = os.path.join(PERSISTENT_STORAGE_PATH, 'conversations.json')

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}

# --- SESSION MANAGEMENT ---
# A secret key is required for sessions to work. It should be a long, random string.
# In a production environment, load this from an environment variable.
app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'a-very-long-and-random-secret-key-in-dev')
if app.secret_key == 'a-very-long-and-random-secret-key-in-dev' and not app.debug:
    print("WARNING: FLASK_SECRET_KEY is not set. Using a default, insecure key for production.")
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=30)  # Set "Remember Me" duration

# --- Route to serve files from persistent disk ---
@app.route('/uploads/<path:filename>')
def serve_upload(filename):
    """Serves files from the persistent image upload directory."""
    return send_from_directory(app.config['IMAGE_UPLOADS_ROOT'], filename)

def get_all_users():
    """Helper to read all users from the JSON file."""
    try:
        with open(USERS_FILE, 'r') as f:
            return json.load(f)
    except (IOError, json.JSONDecodeError):
        return []

# --- NEW: Ads Management Helpers ---
def get_all_ads():
    """Helper to read all ads from the JSON file."""
    if not os.path.exists(ADS_FILE):
        return []
    try:
        with open(ADS_FILE, 'r') as f:
            return json.load(f)
    except (IOError, json.JSONDecodeError):
        return []

def save_all_ads(ads_data):
    """Helper to save all ads to the JSON file."""
    os.makedirs(os.path.dirname(ADS_FILE), exist_ok=True)
    with open(ADS_FILE, 'w') as f:
        json.dump(ads_data, f, indent=4)

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

    current_user = next((u for u in get_all_users() if u.get('id') == user_id), None)
    if not current_user or current_user.get('role') != 'admin':
        # If not an admin, redirect to home page.
        return redirect(url_for('index'))

    _, total_unread_count = get_user_and_unread_count(session)
    
    # NEW: Fetch ads data for the main admin
    ads = []
    if current_user.get('email') == 'qrbxb70@gmail.com':
        ads = get_all_ads()
    return render_template('index.html', user=current_user, total_unread_count=total_unread_count, ads=ads)

@app.route('/parks', methods=['GET'])
def get_parks():
    """Endpoint to get all parks, filtered by admin role."""
    user_id = session.get('user_id')
    if not user_id:
        return jsonify({"error": "Authentication required"}), 401

    current_user = next((u for u in get_all_users() if u.get('id') == user_id), None)
    if not current_user or current_user.get('role') != 'admin':
        return jsonify({"error": "Admin privileges required"}), 403

    all_parks = data_manager.get_all_parks()
    if current_user.get('email') == 'qrbxb70@gmail.com':
        return jsonify(all_parks)
    user_parks = [park for park in all_parks if park.get('admin_id') == user_id]
    return jsonify(user_parks)

@app.route('/parks', methods=['POST'])
def create_park():
    """Endpoint to create a new park with an image upload."""
    user_id = session.get('user_id')
    if not user_id:
        return jsonify({"error": "Authentication required"}), 401
    current_user = next((u for u in get_all_users() if u.get('id') == user_id), None)
    if not current_user or current_user.get('role') != 'admin':
        return jsonify({"error": "Admin privileges required to post products."}), 403

    park_data = request.form.to_dict()
    park_data['home_delivery'] = 'home_delivery' in request.form # This will be True or False
    required_fields = ['name', 'location', 'price', 'description', 'type']
    if not all(field in park_data for field in required_fields):
        return jsonify({"error": f"Missing one or more required fields: {required_fields}"}), 400

    # --- Handle Multiple Image Uploads ---
    uploaded_files = []
    for i in range(1, 5):
        file = request.files.get(f'image{i}')
        if file and file.filename:
            if not allowed_file(file.filename):
                return jsonify({"error": f"Invalid file type for image {i}. Allowed: {list(ALLOWED_EXTENSIONS)}"}), 400
            uploaded_files.append(file)

    # For new products, require at least 2 images
    if len(uploaded_files) < 2:
        return jsonify({"error": "At least two images are required for a new product."}), 400

    extensions = [secure_filename(f.filename).rsplit('.', 1)[1].lower() for f in uploaded_files]
    # --- End Image Handling ---

    os.makedirs(app.config['IMAGE_UPLOADS_ROOT'], exist_ok=True)
    os.makedirs(USER_UPLOADS_DIR, exist_ok=True)

    try:
        # 1. The data_manager.add_park function must be updated to accept a list of extensions
        # and return a list of filenames in the 'image_filenames' key.
        new_park = data_manager.add_park(park_data, extensions, user_id)
        
        # 2. Save all the uploaded image files using the filenames from the created record.
        final_filenames = new_park.get('image_filenames', [])
        if len(final_filenames) != len(uploaded_files):
            # This is a failsafe. If this happens, data_manager.add_park was not updated correctly.
            # We should probably delete the DB entry here, but for now, we'll return an error.
            data_manager.delete_park(new_park['id']) # Attempt to clean up
            return jsonify({"error": "Mismatch between uploaded files and generated filenames."}), 500

        lqip_filenames = [] # To store the names of the low-quality placeholders

        for i, file_storage in enumerate(uploaded_files):
            full_filename = final_filenames[i]
            image_path = os.path.join(app.config['IMAGE_UPLOADS_ROOT'], full_filename)
            
            # Open the uploaded image file with Pillow
            img = Image.open(file_storage.stream).convert("RGB") # Ensure RGB for JPEG saving
            # Resize to a max of 800x800 while maintaining aspect ratio
            img.thumbnail((800, 800))
            # Save with high quality.
            img.save(image_path, 'JPEG', quality=90, optimize=True)

            # --- Create and save the Low Quality Image Placeholder (LQIP) ---
            lqip_filename = full_filename.rsplit('.', 1)[0] + '.lqip.jpg'
            lqip_path = os.path.join(app.config['IMAGE_UPLOADS_ROOT'], lqip_filename)
            file_storage.stream.seek(0) # Reset file stream to be read again
            lqip_img = Image.open(file_storage.stream).convert("RGB")
            lqip_img.thumbnail((32, 32)) # Create a very small thumbnail
            lqip_img.save(lqip_path, 'JPEG', quality=20) # Save at very low quality
            lqip_filenames.append(lqip_filename)

        # Update the park data with the list of LQIP filenames
        # This assumes data_manager.update_park can add this new field.
        data_manager.update_park(new_park['id'], {'image_filenames_lqip': lqip_filenames}, None)
        new_park['image_filenames_lqip'] = lqip_filenames
        return jsonify(new_park), 201
    except Exception as e:
        # Basic error handling in case something goes wrong
        # In a production app, you might want to log this error instead of sending details to the client.
        return jsonify({"error": "An internal error occurred.", "details": str(e)}), 500

@app.route('/parks/<string:park_id>', methods=['PUT'])
def update_park_details(park_id):
    """Updates an existing park's details."""
    user_id = session.get('user_id')
    if not user_id:
        return jsonify({"error": "Authentication required"}), 401
    
    current_user = next((u for u in get_all_users() if u.get('id') == user_id), None)
    if not current_user or current_user.get('role') != 'admin':
        return jsonify({"error": "Admin privileges required"}), 403

    product_to_update = data_manager.get_park_by_id(park_id)
    if not product_to_update:
        return jsonify({"error": "Park not found"}), 404

    is_main_admin = current_user.get('email') == 'qrbxb70@gmail.com'
    is_product_owner = product_to_update.get('admin_id') == user_id

    if not is_main_admin and not is_product_owner:
        return jsonify({"error": "You do not have permission to edit this product."}), 403

    update_data = request.form.to_dict()
    update_data['home_delivery'] = 'home_delivery' in request.form
    
    # --- NEW, ROBUST IMAGE HANDLING LOGIC ---
    # This logic handles adding/replacing individual images without deleting others.
    
    # Start with a copy of the existing filenames
    current_filenames = product_to_update.get('image_filenames', [])
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
            generated_filename = f"{park_id}_{i+1}.{extension}"
            
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
        # We call update_park with extensions=None because we have handled the
        # filename logic ourselves. This call will just update the product's
        # data in the JSON file with our new `image_filenames` list.
        updated_park, _ = data_manager.update_park(park_id, update_data, None)

        # Now that the JSON is updated, handle the file system changes
        for old_filename in files_to_delete:
            old_image_path = os.path.join(app.config['IMAGE_UPLOADS_ROOT'], old_filename)
            if os.path.exists(old_image_path):
                os.remove(old_image_path)
            # Also delete the corresponding LQIP file
            old_lqip_path = os.path.join(app.config['IMAGE_UPLOADS_ROOT'], old_filename.rsplit('.', 1)[0] + '.lqip.jpg')
            if os.path.exists(old_lqip_path):
                os.remove(old_lqip_path)
        
        for item in files_to_save:
            image_path = os.path.join(app.config['IMAGE_UPLOADS_ROOT'], item['filename'])
            img = Image.open(item['file_storage'].stream).convert("RGB")
            img.thumbnail((800, 800))
            img.save(image_path, 'JPEG', quality=90, optimize=True)
            # Also save the new LQIP file
            lqip_path = os.path.join(app.config['IMAGE_UPLOADS_ROOT'], item['filename'].rsplit('.', 1)[0] + '.lqip.jpg')
            item['file_storage'].stream.seek(0)
            lqip_img = Image.open(item['file_storage'].stream).convert("RGB")
            lqip_img.thumbnail((32, 32))
            lqip_img.save(lqip_path, 'JPEG', quality=20)

        return jsonify(updated_park), 200
    except Exception as e:
        return jsonify({"error": "An internal error occurred during update.", "details": str(e)}), 500

@app.route('/parks/<string:park_id>', methods=['DELETE'])
def remove_park(park_id):
    """Endpoint to delete a park by its ID."""
    user_id = session.get('user_id')
    if not user_id:
        return jsonify({"error": "Authentication required"}), 401
    
    current_user = next((u for u in get_all_users() if u.get('id') == user_id), None)
    if not current_user or current_user.get('role') != 'admin':
        return jsonify({"error": "Admin privileges required"}), 403

    product_to_delete = data_manager.get_park_by_id(park_id)
    if not product_to_delete:
        return jsonify({"error": f"Park with id {park_id} not found."}), 404

    is_main_admin = current_user.get('email') == 'qrbxb70@gmail.com'
    is_product_owner = product_to_delete.get('admin_id') == user_id

    if not is_main_admin and not is_product_owner:
        return jsonify({"error": "You do not have permission to delete this product."}), 403

    deleted_park = data_manager.delete_park(park_id)
    if deleted_park:
        # If images are associated, delete them from the file system
        image_filenames = deleted_park.get('image_filenames', [])
        if image_filenames:
            for filename in image_filenames:
                image_path = os.path.join(app.config['IMAGE_UPLOADS_ROOT'], filename)
                if os.path.exists(image_path):
                    os.remove(image_path)
                # Also delete the LQIP file
                lqip_filename = filename.rsplit('.', 1)[0] + '.lqip.jpg'
                lqip_path = os.path.join(app.config['IMAGE_UPLOADS_ROOT'], lqip_filename)
                if os.path.exists(lqip_path):
                    os.remove(lqip_path)
                
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

# --- NEW: API and Management Routes for Ads ---

@app.route('/api/ads', methods=['GET'])
def get_ads():
    """Endpoint to get all ads for the shop page carousel."""
    ads = get_all_ads()
    # Add the full image path for the frontend
    for ad in ads:
        ad['image_url'] = f"/uploads/ads/{ad['image_filename']}"
    return jsonify(ads)

@app.route('/ads', methods=['POST'])
def create_ad():
    """Endpoint for the main admin to create a new ad."""
    user_id = session.get('user_id')
    if not user_id:
        return jsonify({"error": "Authentication required"}), 401
    
    current_user = next((u for u in get_all_users() if u.get('id') == user_id), None)
    # Only the main admin can create ads
    if not current_user or current_user.get('email') != 'qrbxb70@gmail.com':
        return jsonify({"error": "Super admin privileges required."}), 403

    ad_data = request.form.to_dict()
    required_fields = ['name', 'description']
    if not all(field in ad_data for field in required_fields):
        return jsonify({"error": "Missing name or description"}), 400

    image_file = request.files.get('image')
    if not image_file or not image_file.filename:
        return jsonify({"error": "Image is required"}), 400

    if not allowed_file(image_file.filename):
        return jsonify({"error": f"Invalid file type. Allowed: {list(ALLOWED_EXTENSIONS)}"}), 400

    os.makedirs(app.config['ADS_UPLOADS_DIR'], exist_ok=True)
    
    ads = get_all_ads()
    
    # Generate new ID
    new_id = (max(int(ad['id']) for ad in ads) + 1) if ads else 1
    
    # Generate filename
    extension = secure_filename(image_file.filename).rsplit('.', 1)[1].lower()
    filename = f"ad_{new_id}.{extension}"
    
    # Save the image file WITHOUT compression
    image_path = os.path.join(app.config['ADS_UPLOADS_DIR'], filename)
    image_file.save(image_path)

    new_ad = {
        "id": str(new_id),
        "name": ad_data['name'],
        "description": ad_data['description'],
        "image_filename": filename,
        "date_added": datetime.now(timezone.utc).isoformat()
    }
    
    ads.append(new_ad)
    save_all_ads(ads)
    
    return jsonify(new_ad), 201

@app.route('/ads/<string:ad_id>', methods=['DELETE'])
def remove_ad(ad_id):
    """Endpoint for the main admin to delete an ad."""
    user_id = session.get('user_id')
    if not user_id:
        return jsonify({"error": "Authentication required"}), 401
    
    current_user = next((u for u in get_all_users() if u.get('id') == user_id), None)
    if not current_user or current_user.get('email') != 'qrbxb70@gmail.com':
        return jsonify({"error": "Super admin privileges required."}), 403

    ads = get_all_ads()
    ad_to_delete = next((ad for ad in ads if ad['id'] == ad_id), None)

    if not ad_to_delete:
        return jsonify({"error": "Ad not found"}), 404

    # Delete the image file
    image_path = os.path.join(app.config['ADS_UPLOADS_DIR'], ad_to_delete['image_filename'])
    if os.path.exists(image_path):
        os.remove(image_path)

    # Remove from the list and save
    updated_ads = [ad for ad in ads if ad['id'] != ad_id]
    save_all_ads(updated_ads)

    return jsonify({"message": f"Ad with id {ad_id} deleted."}), 200
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
    """Helper to get the current user and their total unread message count."""
    current_user = None
    user_id = session.get('user_id')
    total_unread_count = 0
    if user_id:
        users = get_all_users()
        current_user = next((user for user in users if user.get('id') == user_id), None)
        if current_user:
            conversations = get_conversations()
            if current_user.get('role') == 'admin':
                # Admin's total is the sum of unread messages in conversations they are part of.
                for key, convo_data in conversations.items():
                    # Handle both old and new data structures
                    if isinstance(convo_data, dict):
                        messages = convo_data.get('messages', [])
                        deleted_by = convo_data.get('deleted_by', [])
                        if user_id in deleted_by:
                            continue # Skip chats deleted by this admin
                    else:
                        messages = convo_data

                    if key.endswith(f"-{user_id}"): # Check if the admin is the recipient
                        total_unread_count += sum(1 for msg in messages if msg.get('sender') == 'user' and not msg.get('seen'))
            else:
                # User's total is the sum of unread messages from admins across all their conversations.
                for key, convo_data in conversations.items():
                    if isinstance(convo_data, dict):
                        messages = convo_data.get('messages', [])
                        deleted_by = convo_data.get('deleted_by', [])
                        if user_id in deleted_by:
                            continue # Skip chats deleted by this user
                    else:
                        messages = convo_data

                    if key.startswith(f"{user_id}-"): # Check if the user is the sender
                        total_unread_count += sum(1 for msg in messages if msg.get('sender') == 'admin' and not msg.get('seen'))
    return current_user, total_unread_count

def get_conversations():
    """Reads all chat conversations from the JSON file."""
    if not os.path.exists(CONVERSATIONS_FILE):
        return {}
    try:
        with open(CONVERSATIONS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (json.JSONDecodeError, FileNotFoundError):
        return {}

def save_conversations(conversations):
    """Saves the conversations dictionary to the JSON file."""
    os.makedirs(os.path.dirname(CONVERSATIONS_FILE), exist_ok=True)
    with open(CONVERSATIONS_FILE, 'w', encoding='utf-8') as f:
        json.dump(conversations, f, indent=2)

@app.route('/chat')
def chat_page():
    """Acts as a router, redirecting users to their appropriate chat dashboard."""
    user_id = session.get('user_id')
    if not user_id:
        return redirect(url_for('index', _anchor='login'))

    users = get_all_users()
    current_user = next((user for user in users if user.get('id') == user_id), None)
    if not current_user:
        session.clear()
        return redirect(url_for('index', _anchor='login'))

    # Check for different chat initiation parameters
    is_support_request = request.args.get('support') == 'true'
    product_id = request.args.get('product_id')
    target_admin_id_from_param = request.args.get('admin_id')
    report_merchant_id = request.args.get('report_merchant_id')
    report_merchant_name = request.args.get('report_merchant_name')

    target_admin_id = None
    main_admin = None

    # A report is a type of support request, so it should also go to the main admin
    if is_support_request or report_merchant_id:
        # Find the main admin (customer service)
        main_admin = next((u for u in users if u.get('email') == 'qrbxb70@gmail.com'), None)
        if main_admin:
            target_admin_id = main_admin.get('id')

    elif target_admin_id_from_param:
        target_admin_id = target_admin_id_from_param
    elif product_id:
        product = data_manager.get_park_by_id(product_id)
        if product:
            target_admin_id = product.get('admin_id')
            # Inquiry tracking logic
            if product.get('admin_id') != user_id:
                inquired_session_key = f'inquired_{product_id}'
                if not session.get(inquired_session_key):
                    data_manager.increment_product_inquiry(product_id)
                    session[inquired_session_key] = True

    # --- NEW LOGIC: Ensure conversation exists before redirecting ---
    # This guarantees that when the user lands on their dashboard, the
    # conversation item will be in the list, ready to be opened.
    if target_admin_id and target_admin_id != user_id:
        conversation_key = None
        target_user = next((u for u in users if u.get('id') == target_admin_id), None)

        if target_user: # Ensure the target user exists
            # Determine the correct conversation key format.
            # The goal is a consistent key regardless of who starts the chat.
            is_initiator_admin = current_user.get('role') == 'admin'
            is_target_admin = target_user.get('role') == 'admin'

            if is_initiator_admin and not is_target_admin:
                # Admin starting chat with a normal user. Key is user-admin.
                conversation_key = f"{target_admin_id}-{user_id}"
            elif not is_initiator_admin and is_target_admin:
                # Normal user starting chat with an admin. Key is user-admin.
                conversation_key = f"{user_id}-{target_admin_id}"
            else: # Admin-to-admin or other cases. Use a sorted, canonical key.
                conversation_key = '-'.join(sorted([user_id, target_admin_id]))
            
            conversations = get_conversations()
            if conversation_key not in conversations:
                conversations[conversation_key] = {"messages": [], "deleted_by": []}
                save_conversations(conversations)

    # --- NEW LOGIC: Handle auto-messaging for reports ---
    if report_merchant_id and report_merchant_name and target_admin_id and main_admin:
        conversation_key = f"{user_id}-{target_admin_id}"
        conversations = get_conversations()

        # Construct the report message
        report_text = (
            f"--- AUTOMATED REPORT ---\n"
            f"User '{current_user['name']}' ({current_user['id']}) is reporting a store.\n\n"
            f"Store Name: {report_merchant_name}\n"
            f"Store ID: {report_merchant_id}"
        )
        
        # Create the message object. It's sent from the 'user' to the 'admin'.
        new_message = {
            "sender": "user", # The user is sending the report
            "text": report_text,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "seen": False, # The main admin has not seen it yet
            "conversation_id": conversation_key,
            "is_system": True # Flag to identify this as an automated message
        }
        
        # Add user/admin info to the message for the frontend
        new_message['user_info'] = {'id': current_user['id'], 'name': current_user['name'], 'photo': current_user.get('photo')}
        new_message['admin_info'] = {'id': main_admin['id'], 'name': main_admin['name'], 'photo': main_admin.get('photo')}

        conversations.setdefault(conversation_key, {"messages": [], "deleted_by": []})['messages'].append(new_message)
        save_conversations(conversations)
        socketio.emit('receive_message', new_message, room=target_admin_id)

    # --- END NEW LOGIC ---

    if current_user.get('role') == 'admin':
        if target_admin_id and target_admin_id != user_id:
            # Admin wants to chat with another user. The `conversation_key` has already
            # been calculated and the conversation created. We just need to redirect.
            return redirect(url_for('admin_chat_dashboard', open_convo=conversation_key))
        # Otherwise, just go to the admin dashboard
        return redirect(url_for('admin_chat_dashboard'))
    else:
        # Normal user, redirect to their dashboard
        if target_admin_id:
            # Redirect to the user's chat page and pass the admin's ID
            # The JS on that page will use this ID to find and open the correct conversation.
            return redirect(url_for('my_chats_page', open_convo_with_admin=target_admin_id))
        return redirect(url_for('my_chats_page'))

@app.route('/admin/chat')
def admin_chat_dashboard():
    """Serves the admin chat dashboard, showing all conversations they are part of."""
    user_id = session.get('user_id')
    if not user_id:
        return redirect(url_for('index', _anchor='login'))

    users = get_all_users()
    current_user = next((user for user in users if user.get('id') == user_id), None)
    if not current_user or current_user.get('role') != 'admin':
        return redirect(url_for('index'))

    is_main_admin = current_user.get('email') == 'qrbxb70@gmail.com'
    admin_id = current_user['id']
    user_map = {u['id']: u for u in users}
    
    conversations = get_conversations()
    visible_conversations = {}
    convo_list = []

    for key, convo_data in conversations.items():
        try:
            user_part, admin_part = key.split('-')
        except ValueError:
            continue

        # Show conversations where the current admin is EITHER participant
        if is_main_admin or admin_id in [user_part, admin_part]:
            if isinstance(convo_data, dict):
                messages = convo_data.get('messages', [])
                deleted_by = convo_data.get('deleted_by', [])
                if admin_id in deleted_by:
                    continue
            else:
                messages = convo_data
            
            # if not messages: continue # Allow empty conversations to be rendered

            visible_conversations[key] = messages
            
            # Determine who the "other person" is in the chat
            other_user_id = user_part if admin_id == admin_part else admin_part
            other_user_info = user_map.get(other_user_id)
            if not other_user_info: continue

            if messages:
                last_message = messages[-1]
                clean_text = re.sub(r'<[^>]+>', ' ', last_message.get('text', '')).strip() or '[Product Link]'
                last_message_time = last_message.get('timestamp')
                # Determine which messages are "from the other person" to count as unread
                unread_sender_type = 'user' if admin_id == admin_part else 'admin'
                unread_count = sum(1 for msg in messages if msg.get('sender') == unread_sender_type and not msg.get('seen'))
            else:
                clean_text = "New conversation"
                last_message_time = datetime.now(timezone.utc).isoformat()
                unread_count = 0
            
            convo_list.append({
                "id": key,
                "user_name": other_user_info['name'], # Always show the other person's name
                "user_photo": other_user_info.get('photo'),
                "user_id": other_user_info['id'],
                "admin_name": user_map.get(admin_part, {}).get('name', 'N/A'), # The merchant in this specific convo
                "last_message_text": clean_text,
                "last_message_time": last_message_time,
                "unread_count": unread_count
            })

    convo_list.sort(key=lambda x: x['last_message_time'] or '', reverse=True)
    return render_template('admin_chat.html', user=current_user, convo_list=convo_list, all_conversations=visible_conversations, is_main_admin=is_main_admin, user_map=user_map)

@app.route('/api/conversations/mark_seen', methods=['POST'])
def mark_as_seen():
    """API endpoint for an admin to mark user messages as seen."""
    user_id = session.get('user_id')
    if not user_id:
        return jsonify({"error": "Not authenticated"}), 401
    
    current_user = next((u for u in get_all_users() if u.get('id') == user_id), None)
    if not current_user or current_user.get('role') != 'admin':
        return jsonify({"error": "Forbidden"}), 403

    data = request.get_json()
    conversation_key = data.get('conversation_key')
    if not conversation_key:
        return jsonify({"error": "conversation_key is required"}), 400

    conversations = get_conversations()
    convo_data = conversations.get(conversation_key)

    if not convo_data:
        return jsonify({"error": "Conversation not found"}), 404

    # Handle both data structures
    if isinstance(convo_data, dict):
        messages = convo_data.get('messages', [])
    else:
        messages = convo_data

    updated = False
    for message in messages:
        if message.get('sender') == 'user' and not message.get('seen'):
            message['seen'] = True
            updated = True
    
    if updated:
        # Ensure it's saved in the new format
        conversations.setdefault(conversation_key, {'messages': [], 'deleted_by': []})['messages'] = messages
        save_conversations(conversations)

    return jsonify({"message": "Messages marked as seen"}), 200

@app.route('/api/conversation-history/<string:conversation_key>')
def get_conversation_history(conversation_key):
    """API endpoint to get the message history for a conversation."""
    user_id = session.get('user_id')
    if not user_id:
        return jsonify({"error": "Not authenticated"}), 401

    conversations = get_conversations()
    convo_data = conversations.get(conversation_key)

    if not convo_data:
        return jsonify([]) # Return empty list if no history

    # Handle both data structures
    if isinstance(convo_data, dict):
        messages = convo_data.get('messages', [])
        deleted_by = convo_data.get('deleted_by', [])
        if user_id in deleted_by:
            return jsonify([]) # User has deleted it, return empty
    else: # Old format
        messages = convo_data

    updated = False
    current_user = next((u for u in get_all_users() if u.get('id') == user_id), None)
    
    if current_user:
        # Determine who the other party is to mark their messages as seen
        # If the current user is a normal user, they are marking admin messages as seen.
        # If the current user is an admin, they are marking user messages as seen.
        other_party_type = 'admin' if current_user.get('role') != 'admin' else 'user'
        for message in messages:
            if message.get('sender') == other_party_type and not message.get('seen'):
                message['seen'] = True
                updated = True
    
    if updated:
        # Ensure it's saved in the new format and save only once after the loop.
        conversations.setdefault(conversation_key, {'messages': [], 'deleted_by': []})['messages'] = messages
        save_conversations(conversations)

    return jsonify(messages)

@app.route('/api/conversation/<string:conversation_id>/delete', methods=['POST'])
def delete_conversation(conversation_id):
    user_id = session.get('user_id')
    if not user_id:
        return jsonify({"error": "Not authenticated"}), 401

    conversations = get_conversations()
    if conversation_id not in conversations:
        return jsonify({"error": "Conversation not found"}), 404

    convo_data = conversations[conversation_id]
    if isinstance(convo_data, list): # Migrate old format on the fly
        convo_data = {"messages": convo_data, "deleted_by": []}

    deleted_by_list = convo_data.get('deleted_by', [])
    if user_id not in deleted_by_list:
        deleted_by_list.append(user_id)

    # Check if both participants have deleted the chat to permanently remove it
    try:
        user_part, admin_part = conversation_id.split('-')
        if user_part in deleted_by_list and admin_part in deleted_by_list:
            del conversations[conversation_id]
        else:
            convo_data['deleted_by'] = deleted_by_list
            conversations[conversation_id] = convo_data
    except ValueError:
        # Handle malformed key, maybe just delete it
        if conversation_id in conversations:
            del conversations[conversation_id]

    save_conversations(conversations)
    return jsonify({"message": "Chat hidden successfully"}), 200

@socketio.on('connect')
def handle_connect():
    """Handles a new WebSocket connection."""
    user_id = session.get('user_id')
    if not user_id:
        return # Silently ignore unauthenticated connections
    join_room(user_id)
@socketio.on('new_message')
def handle_new_message(data):
    """Handles receiving a new message from a client."""
    user_id = session.get('user_id')
    if not user_id:
        return # Not authenticated

    current_user = next((u for u in get_all_users() if u.get('id') == user_id), None)
    if not current_user:
        return

    text = data.get('text')
    conversation_key = data.get('conversation_id')

    if not text or not conversation_key:
        return # Ignore invalid messages

    # Determine sender type and target room from the provided key, which is the source of truth.
    try:
        user_part, admin_part = conversation_key.split('-')
    except ValueError:
        return # Invalid key format

    if user_id == user_part:
        sender_type = 'user'
        target_room = admin_part
    elif user_id == admin_part:
        sender_type = 'admin'
        target_room = user_part
    else:
        # The sender is not part of this conversation. This is a security check.
        return

    new_message = {"sender": sender_type, "text": text, "timestamp": datetime.now(timezone.utc).isoformat(), "seen": False, "conversation_id": conversation_key}    
    # --- NEW: Always attach current user info to the message payload ---
    # This ensures the frontend always has the latest name, solving the identity bug.
    all_users = get_all_users()
    user_part_id, admin_part_id = conversation_key.split('-')
    user_info = next((u for u in all_users if u.get('id') == user_part_id), None)
    admin_info = next((u for u in all_users if u.get('id') == admin_part_id), None)

    if user_info and admin_info:
        new_message['user_info'] = {'id': user_info['id'], 'name': user_info['name'], 'photo': user_info.get('photo')}
        new_message['admin_info'] = {'id': admin_info['id'], 'name': admin_info['name'], 'photo': admin_info.get('photo')}

    conversations = get_conversations()
    
    # --- Handle new data structure and undelete logic ---
    convo_data = conversations.get(conversation_key)
    if convo_data is None:
        convo_data = {"messages": [], "deleted_by": []}
    elif isinstance(convo_data, list): # Migrate old format
        convo_data = {"messages": convo_data, "deleted_by": []}

    # If the recipient had deleted the chat, this new message "resurrects" it for them.
    recipient_id = target_room
    if recipient_id in convo_data.get('deleted_by', []):
        convo_data['deleted_by'].remove(recipient_id)

    convo_data['messages'].append(new_message)
    conversations[conversation_key] = convo_data
    save_conversations(conversations)

    # --- Real-time message distribution ---
    # Send to the target (the other person in the chat)
    emit('receive_message', new_message, room=target_room)
    # Send back to the sender for their own UI
    emit('receive_message', new_message, room=user_id)

    # The main admin needs to see everything in real-time.
    main_admin = next((u for u in get_all_users() if u.get('email') == 'qrbxb70@gmail.com'), None)
    if main_admin and main_admin['id'] not in [user_id, target_room]:
        emit('receive_message', new_message, room=main_admin['id'])


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

    try:
        users = get_all_users()
        user_found = next((user for user in users if user.get('email') == email), None)

        # Securely check the hashed password
        if user_found and check_password_hash(user_found.get('password'), password):
            # Store the user's ID in the session to "log them in"
            session['user_id'] = user_found.get('id')
            # If "Remember Me" was checked, make the session permanent.
            if remember_me:
                session.permanent = True

            return jsonify({"message": "Login successful", "user_id": user_found.get('id')}), 200
        else:
            return jsonify({"error": "Invalid email or password"}), 401

    except (IOError, json.JSONDecodeError):
        return jsonify({"error": "An internal server error occurred"}), 500


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

    try:
        # Read existing users from the JSON file
        users = get_all_users()

        # Generate a new unique ID, handling potentially zero-padded string IDs
        if users:
            # Safely convert string IDs to integers to find the maximum
            ids = []
            for user in users:
                try: ids.append(int(user.get('id')))
                except (ValueError, TypeError): continue # Skip if ID is not a valid integer string
            max_id = max(ids) if ids else 0
            new_id = max_id + 1
        else:
            new_id = 1

        # Format the ID as a 9-digit zero-padded string (e.g., "000000001")
        formatted_id = f"{new_id:09d}"

        # Create the new user dictionary
        new_user = {
            "id": formatted_id,
            "name": name,
            "email": email,
            "number": data.get('number'),
            "location": data.get('location'),
            "password": generate_password_hash(password), # Hash the password
            "photo": data.get('photo'),
            # New users are assigned the 'normal' role by default.
            "role": "normal"
        }

        # Add the new user to the list and write back to the file
        users.append(new_user)
        with open(USERS_FILE, 'w') as f:
            json.dump(users, f, indent=4)

        return jsonify({"message": "User created successfully", "user": new_user}), 201

    except (IOError, json.JSONDecodeError) as e:
        return jsonify({"error": "An internal server error occurred"}), 500

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

    users = get_all_users()
    user_index = -1
    for i, u in enumerate(users):
        if u.get('id') == user_id:
            user_index = i
            break
    
    if user_index == -1:
        return jsonify({"error": "User not found"}), 404

    current_user = users[user_index]
    
    # Update text fields
    current_user['name'] = request.form.get('name', current_user['name'])
    current_user['number'] = request.form.get('number', current_user.get('number'))

    # Handle photo upload
    photo = request.files.get('photo')
    if photo and photo.filename != '':
        if not allowed_file(photo.filename):
            return jsonify({"error": "Invalid file type for photo"}), 400
        
        os.makedirs(USER_UPLOADS_DIR, exist_ok=True)
        
        # Create a unique filename based on user ID to prevent conflicts
        extension = secure_filename(photo.filename).rsplit('.', 1)[1].lower()
        new_filename = f"user_{user_id}.{extension}"
        new_filepath = os.path.join(USER_UPLOADS_DIR, new_filename)

        # Delete old photo if it exists and has a different name
        old_photo_path = current_user.get('photo')
        if old_photo_path and old_photo_path != f"/uploads/users/{new_filename}":
            # Construct full path from web path
            # e.g., /uploads/users/user_123.jpg -> users/user_123.jpg
            old_relative_path = os.path.join(*old_photo_path.strip('/').split('/')[1:])
            old_fs_path = os.path.join(app.config['IMAGE_UPLOADS_ROOT'], old_relative_path)
            if os.path.exists(old_fs_path):
                os.remove(old_fs_path)

        # Save the new photo
        img = Image.open(photo.stream).convert("RGB")
        img.thumbnail((200, 200)) # Resize profile pictures
        img.save(new_filepath, 'JPEG', quality=90)

        # Store the web-accessible path
        current_user['photo'] = f"/uploads/users/{new_filename}"

    # Save the updated user list
    users[user_index] = current_user
    with open(USERS_FILE, 'w') as f:
        json.dump(users, f, indent=4)

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

    users = get_all_users()
    user_index = next((i for i, u in enumerate(users) if u.get('id') == user_id), -1)

    if user_index == -1:
        return jsonify({"error": "User not found"}), 404

    user_to_upgrade = users[user_index]
    if user_to_upgrade.get('role') != 'normal':
        return jsonify({"error": "User is already a merchant or has a different role."}), 400

    # --- Update user data from form ---
    user_to_upgrade['name'] = request.form.get('store_name', user_to_upgrade['name'])
    user_to_upgrade['number'] = request.form.get('phone_number', user_to_upgrade.get('number'))
    
    # Construct location from mandatory IP city and optional map address
    ip_city = request.form.get('ip_city')
    map_address = request.form.get('map_address')
    if map_address:
        user_to_upgrade['location'] = map_address
    elif ip_city:
        user_to_upgrade['location'] = ip_city

    # Handle optional photo upload
    photo = request.files.get('photo')
    if photo and photo.filename != '':
        if not allowed_file(photo.filename):
            return jsonify({"error": "Invalid file type for photo"}), 400
        
        # This logic is similar to update_profile
        os.makedirs(USER_UPLOADS_DIR, exist_ok=True)
        extension = secure_filename(photo.filename).rsplit('.', 1)[1].lower()
        new_filename = f"user_{user_id}.{extension}"
        new_filepath = os.path.join(USER_UPLOADS_DIR, new_filename)
        img = Image.open(photo.stream).convert("RGB")
        img.thumbnail((200, 200))
        img.save(new_filepath, 'JPEG', quality=90)
        user_to_upgrade['photo'] = f"/uploads/users/{new_filename}"

    # Upgrade role and set initial rating
    user_to_upgrade['role'] = 'admin'
    user_to_upgrade['ratings_total'] = 5
    user_to_upgrade['ratings_count'] = 1
    user_to_upgrade['reviews'] = []

    # Save the updated user list
    users[user_index] = user_to_upgrade
    with open(USERS_FILE, 'w') as f:
        json.dump(users, f, indent=4)

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
    product_admin = next((user for user in get_all_users() if user.get('id') == product.get('admin_id')), None)
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
    image_filenames = product.get('image_filenames', [])
    product['images'] = [f"/uploads/{fname}" for fname in image_filenames if fname]

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

    all_users = get_all_users()
    merchant_index = -1
    for i, u in enumerate(all_users):
        if u.get('id') == merchant_id and u.get('role') == 'admin':
            merchant_index = i
            break
    
    if merchant_index == -1:
        return jsonify({"error": "Merchant not found."}), 404

    if user_id == merchant_id:
        return jsonify({"error": "You cannot review your own store."}), 403

    merchant = all_users[merchant_index]
    
    merchant['ratings_total'] = merchant.get('ratings_total', 0) + rating
    merchant['ratings_count'] = merchant.get('ratings_count', 0) + 1
    
    comment = data.get('comment')
    if comment:
        merchant.setdefault('reviews', []).append({
            "user_id": user_id, "comment": comment, "rating": rating,
            "timestamp": datetime.now(timezone.utc).isoformat()
        })

    all_users[merchant_index] = merchant
    with open(USERS_FILE, 'w') as f:
        json.dump(all_users, f, indent=4)

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

    all_users = get_all_users()
    merchant = next((user for user in all_users if user.get('id') == merchant_id and user.get('role') == 'admin'), None)

    if not merchant:
        return "Merchant not found.", 404

    all_products = data_manager.get_all_parks()
    
    # Add the first image filename for easier access in the template
    for product in all_products:
        filenames = product.get('image_filenames')
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

    users = get_all_users()
    current_user = next((user for user in users if user.get('id') == user_id), None)
    if not current_user:
        session.clear()
        return redirect(url_for('index', _anchor='login'))
    
    if current_user.get('role') == 'admin':
        return redirect(url_for('admin_chat_dashboard')) # Admins go to their dashboard

    conversations = get_conversations()
    user_map = {u['id']: u for u in users}
    user_convos_list = []
    all_user_conversations = {}

    for key, convo_data in conversations.items():
        if key.startswith(f"{user_id}-"):
            # Handle new data structure and deletion
            if isinstance(convo_data, dict):
                messages = convo_data.get('messages', [])
                deleted_by = convo_data.get('deleted_by', [])
                if user_id in deleted_by:
                    continue # Don't show conversations this user has deleted
            else: # Old format
                messages = convo_data
            
            all_user_conversations[key] = messages

            try:
                _, admin_part_id = key.split('-')
            except ValueError:
                continue

            admin_info = user_map.get(admin_part_id)
            if not admin_info:
                continue

            if messages:
                last_message = messages[-1]
                clean_text = re.sub(r'<[^>]+>', ' ', last_message.get('text', 'No messages yet.')).strip()
                last_message_time = last_message.get('timestamp')
                unread_count = sum(1 for msg in messages if msg.get('sender') == 'admin' and not msg.get('seen'))
            else:
                clean_text = "Start the conversation!"
                last_message_time = datetime.now(timezone.utc).isoformat()
                unread_count = 0

            user_convos_list.append({
                "id": key,
                "admin_id": admin_info['id'],
                "admin_name": admin_info['name'],
                "admin_photo": admin_info.get('photo'),
                "last_message_text": clean_text,
                "last_message_time": last_message_time,
                "unread_count": unread_count
            })
    user_convos_list.sort(key=lambda x: x['last_message_time'] or '', reverse=True)
    _, total_unread_count = get_user_and_unread_count(session)
    return render_template('my_chats.html', user=current_user, total_unread_count=total_unread_count, convo_list=user_convos_list, all_conversations=all_user_conversations)

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
    # Ensure the data files exist for local development
    # Create persistent storage directory and subdirectories if they don't exist
    os.makedirs(PERSISTENT_STORAGE_PATH, exist_ok=True)
    os.makedirs(IMAGE_UPLOADS_ROOT, exist_ok=True)
    os.makedirs(ADS_UPLOADS_DIR, exist_ok=True)
    os.makedirs(USER_UPLOADS_DIR, exist_ok=True)
    
    # Create empty JSON files if they don't exist
    for file_path, default_content in [
        (USERS_FILE, []), (CONVERSATIONS_FILE, {}), (ADS_FILE, []),
        (data_manager.DATABASE_PATH, [])]:
        if not os.path.isfile(file_path):
            with open(file_path, 'w') as f:
                json.dump(default_content, f)

    # This block is for LOCAL DEVELOPMENT ONLY.
    # In production (like on Render), the `Procfile` uses Gunicorn to run the app.
    # The `debug=True` flag enables auto-reloading and the interactive debugger.
    print("--- Starting DEVELOPMENT server on http://127.0.0.1:5001 (DEBUG MODE) ---")
    socketio.run(app, host='127.0.0.1', port=5001, debug=True)