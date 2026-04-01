# bad.py — Intentional crash for testing Crash-Copilot

def process_data(user_data):
    print("Processing user...")
    return user_data["age"]  # BUG: "age" key does not exist

# Intentional crash
user = {"name": "Ashik", "role": "Engineer"}
process_data(user)
