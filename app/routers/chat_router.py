import uuid
import json
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from ..database import get_db

router = APIRouter(prefix="/chat", tags=["Chat"])

class ConnectionManager:
    def __init__(self):
        self.active_connections: dict[str, list[WebSocket]] = {}

    async def connect(self, room_id: str, websocket: WebSocket):
        await websocket.accept()
        if room_id not in self.active_connections:
            self.active_connections[room_id] = []
        self.active_connections[room_id].append(websocket)

    def disconnect(self, room_id: str, websocket: WebSocket):
        if room_id in self.active_connections:
            self.active_connections[room_id].remove(websocket)

    async def broadcast(self, room_id: str, message: dict):
        dead = []
        for connection in self.active_connections.get(room_id, []):
            try:
                await connection.send_text(json.dumps(message))
            except:
                dead.append(connection)
        for d in dead:
            self.disconnect(room_id, d)

manager = ConnectionManager()

def _now():
    return datetime.now(timezone.utc).isoformat()

@router.get("/{team_id}/messages")
async def get_messages(team_id: str):
    db = get_db()
    cursor = db.chat_messages.find({"team_id": team_id}, {"_id": 0}).sort("timestamp", 1)
    messages = await cursor.to_list(length=300)
    return {"messages": messages}

@router.websocket("/{team_id}/ws/{user_email}")
async def websocket_endpoint(websocket: WebSocket, team_id: str, user_email: str):
    await manager.connect(team_id, websocket)
    db = get_db()
    
    try:
        while True:
            data = await websocket.receive_text()
            payload = json.loads(data)
            
            # check permissions
            team = await db.teams.find_one({"team_id": team_id}, {"_id": 0})
            if team and team.get("admins_only_chat", False):
                is_admin = False
                if team.get("owner_email") == user_email:
                    is_admin = True
                else:
                    member = await db.team_members.find_one({"team_id": team_id, "email": user_email})
                    if member and member.get("role") in ["admin", "owner"]:
                        is_admin = True
                        
                if not is_admin:
                    await websocket.send_text(json.dumps({"error": "Only admins can send messages in this group."}))
                    continue
            
            msg = {
                "msg_id": str(uuid.uuid4()),
                "team_id": team_id,
                "user_email": user_email,
                "user_name": payload.get("user_name", user_email.split('@')[0]),
                "text": payload.get("text", ""),
                "timestamp": _now()
            }
            await db.chat_messages.insert_one(msg.copy())
            msg.pop("_id", None)
            
            await manager.broadcast(team_id, msg)
            
    except WebSocketDisconnect:
        manager.disconnect(team_id, websocket)
