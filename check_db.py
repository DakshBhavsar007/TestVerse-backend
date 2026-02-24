import asyncio
import motor.motor_asyncio

async def main():
    db = motor.motor_asyncio.AsyncIOMotorClient("mongodb://127.0.0.1:27017")["testverse"]
    doc = await db.test_results.find_one(sort=[('_id', -1)])
    if doc:
        print("LOGIN SUCCESS:", doc.get("login_success"))
        print("MESSAGE:", doc.get("login_message"))
        print("STATUS:", doc.get("status"))
        print("ERROR:", doc.get("error"))
    else:
        print("No documents found")

if __name__ == "__main__":
    asyncio.run(main())
