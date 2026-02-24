import asyncio
import motor.motor_asyncio
from app.models import TestResult

async def main():
    db = motor.motor_asyncio.AsyncIOMotorClient("mongodb://127.0.0.1:27017")["testverse"]
    doc = await db.test_results.find_one()
    if doc:
        doc.pop("_id", None)
        try:
            print("Trying to parse doc...")
            tr = TestResult(**doc)
            print("Success!", tr.test_id)
        except Exception as e:
            print("ValidationError!", e)
    else:
        print("No doc found")

asyncio.run(main())
