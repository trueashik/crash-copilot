def process_data(user_data):
    print("Processing user...")
    return user_data["age"]  # Crash! Key does not exist

def main():
    user = {"name": "Ashik", "role": "Engineer"}
    process_data(user)

if __name__ == "__main__":
    main()
