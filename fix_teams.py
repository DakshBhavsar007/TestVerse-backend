from pymongo import MongoClient
import os
from dotenv import load_dotenv

load_dotenv()
client = MongoClient(os.getenv("MONGO_URI", "mongodb://localhost:27017"))
db = client.testverse

teams = list(db.teams.find({}))
for team in teams:
    if team.get("owner_email") == "":
        print(f"Fixing team {team['name']}, owner_id={team['owner_id']}")
        db.teams.update_one({"_id": team["_id"]}, {"$set": {"owner_email": team["owner_id"]}})

members = list(db.team_members.find({}))
for member in members:
    if member.get("email") == "":
        print(f"Fixing member {member['user_id']}, role={member['role']}")
        db.team_members.update_one({"_id": member["_id"]}, {"$set": {"email": member["user_id"]}})

print("Done")
