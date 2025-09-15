#!/usr/bin/env python3

import os
import json
import tempfile
import subprocess
from flask import Flask, request, jsonify
from pathlib import Path

app = Flask(__name__)

# Initialize GPG on startup
def init_gpg():
    """Initialize GPG with mounted keyring"""
    gnupg_home = "/app/gpg"
    
    # Set GPG home to the mounted directory
    os.environ['GNUPGHOME'] = gnupg_home
    
    if not os.path.exists(gnupg_home):
        raise Exception(f"GPG keyring directory {gnupg_home} not found. Please mount the GPG keyring.")
    
    # Check if key exists
    result = subprocess.run(['gpg', '--list-secret-keys'], 
                          capture_output=True, text=True)
    
    if result.returncode != 0 or not result.stdout.strip():
        raise Exception("No GPG secret key found in mounted keyring")
    
    print("Using mounted GPG keyring")
    
    # Get the key ID
    result = subprocess.run(['gpg', '--list-secret-keys', '--with-colons'], 
                          capture_output=True, text=True, check=True)
    
    for line in result.stdout.split('\n'):
        if line.startswith('sec:'):
            return line.split(':')[4]
    
    raise Exception("No GPG key found")

@app.route('/sign', methods=['POST'])
def sign_file():
    """Sign a file and return signature information"""
    try:
        # Get the file from request
        if 'file' not in request.files:
            return jsonify({'error': 'No file provided'}), 400
        
        file = request.files['file']
        if file.filename == '':
            return jsonify({'error': 'No file selected'}), 400
        
        # Save file to temp location
        with tempfile.NamedTemporaryFile(delete=False, suffix='.deb') as temp_file:
            file.save(temp_file.name)
            input_file = temp_file.name
        
        # Create signature file
        signature_file = input_file + '.asc'
        
        try:
            # Sign the file with GPG
            subprocess.run([
                'gpg', '--detach-sign', '--armor', 
                '--output', signature_file,
                input_file
            ], check=True, capture_output=True)
            
            # Read the signature
            with open(signature_file, 'r') as f:
                signature_content = f.read()
            
            return jsonify({
                'signature': signature_file,
                'signature_content': signature_content,
                'status': 'success',
                'key_id': app.config['KEY_ID']
            })
            
        finally:
            # Clean up temp files
            if os.path.exists(input_file):
                os.unlink(input_file)
                
    except Exception as e:
        return jsonify({'error': str(e), 'status': 'failed'}), 500

@app.route('/health', methods=['GET'])
def health():
    """Health check endpoint"""
    return jsonify({'status': 'healthy', 'key_id': app.config.get('KEY_ID', 'unknown')})

@app.route('/public-key', methods=['GET'])
def get_public_key():
    """Export the public key"""
    try:
        key_id = app.config.get('KEY_ID')
        if not key_id:
            return jsonify({'error': 'No key available'}), 500
            
        # Export the public key
        result = subprocess.run([
            'gpg', '--armor', '--export', key_id
        ], capture_output=True, text=True, check=True)
        
        return jsonify({
            'key_id': key_id,
            'public_key': result.stdout,
            'status': 'success'
        })
        
    except Exception as e:
        return jsonify({'error': str(e), 'status': 'failed'}), 500

if __name__ == '__main__':
    print("Initializing signing service...")
    try:
        key_id = init_gpg()
        app.config['KEY_ID'] = key_id
        print(f"Signing service ready with key ID: {key_id}")
        app.run(host='0.0.0.0', port=8080, debug=False)
    except Exception as e:
        print(f"Failed to initialize signing service: {e}")
        exit(1)