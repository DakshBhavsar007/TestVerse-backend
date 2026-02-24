from motor.motor_asyncio import AsyncIOMotorClient
from .config import get_settings

settings = get_settings()

client: AsyncIOMotorClient = None
db = None


async def connect_db():
    global client, db
    client = AsyncIOMotorClient(settings.mongo_uri)
    db = client[settings.mongo_db_name]
    # Create indexes
    await db.test_results.create_index("test_id")
    await db.test_results.create_index("created_at")
    print(f"✅ Connected to MongoDB: {settings.mongo_db_name}")


async def close_db():
    global client
    if client:
        client.close()
        print("❌ MongoDB connection closed")


def get_db():
    return db
