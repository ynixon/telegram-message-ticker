import asyncio
from telethon import TelegramClient

async def main():
    client = TelegramClient('user_session', 5390776, '2df8c2493f52845f2045f035499e837b')
    await client.start(phone='+972507635853')
    dialogs = await client.get_dialogs()
    for d in dialogs:
        if hasattr(d.entity, 'broadcast') or hasattr(d.entity, 'megagroup'):
            print(f'ID: {d.id}, Name: {d.name}, broadcast={getattr(d.entity,"broadcast",False)}, megagroup={getattr(d.entity,"megagroup",False)}')
    await client.disconnect()

asyncio.run(main())
