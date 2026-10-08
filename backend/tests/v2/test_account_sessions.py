from datetime import timedelta
from uuid import uuid4
from sqlalchemy import select
from app.auth.sessions import _hash_token
from app.auth.passwords import hash_password,verify_password
from app.core.config import get_settings
from app.core.time import utc_now
from app.db.models import AuthSession,User
from app.design.models import Wallet
from .test_api import api
from .test_migrations import migration_database
from .test_operations import setup

async def seed(factory,owner,**fields):
 token="session-"+uuid4().hex
 async with factory.begin() as db:
  row=AuthSession(user_id=owner,session_token_hash=_hash_token(token),expires_at=utc_now()+timedelta(hours=1),created_ip="127.0.0.1",**fields)
  db.add(row);await db.flush();identity=row.id
 return token,identity

async def test_revoke_other_sessions_is_private_repeatable_and_keeps_current(api):
 client,factory,owner,design,branch=api
 other,other_id=await seed(factory,owner)
 foreign,_,_=await setup(factory)
 _,foreign_id=await seed(factory,foreign)
 async with factory() as db:
  before=await db.get(Wallet,owner);wallet=(before.balance,before.reserved)
  user=await db.get(User,owner);consent=(user.terms_accepted_at,user.terms_version,user.generation_quality_improvement_consent,user.generation_quality_improvement_consent_updated_at)
 response=await client.post("/api/auth/sessions/revoke-others")
 assert response.status_code==200 and response.json()=={"revoked_sessions":1}
 assert (await client.post("/api/auth/sessions/revoke-others")).json()=={"revoked_sessions":0}
 assert (await client.get("/api/v2/designs/"+design)).status_code==200
 async with factory() as db:
  assert (await db.get(AuthSession,other_id)).revoked_at is not None
  assert (await db.get(AuthSession,foreign_id)).revoked_at is None
  after=await db.get(Wallet,owner);assert (after.balance,after.reserved)==wallet
  user=await db.get(User,owner)
  assert (user.terms_accepted_at,user.terms_version,user.generation_quality_improvement_consent,user.generation_quality_improvement_consent_updated_at)==consent
 client.cookies.clear();client.cookies.set(get_settings().session_cookie_name,other)
 assert (await client.post("/api/auth/sessions/revoke-others")).status_code==401

async def test_revoke_requires_live_non_banned_login(api):
 client,factory,owner,_,_=api
 cookie=client.cookies.get(get_settings().session_cookie_name)
 client.cookies.clear()
 assert (await client.post("/api/auth/sessions/revoke-others")).status_code==401
 client.cookies.set(get_settings().session_cookie_name,cookie)
 async with factory.begin() as db:(await db.get(User,owner)).is_banned=True
 assert (await client.post("/api/auth/sessions/revoke-others")).status_code==403

async def test_change_password_keeps_current_session_and_consent_in_v2(api):
 client,factory,owner,design,_=api
 _,other_id=await seed(factory,owner)
 async with factory.begin() as db:
  user=await db.get(User,owner);user.password_hash=hash_password("old-qa-password")
  old_consent=(user.terms_version,user.generation_quality_improvement_consent)
 rejected=await client.post("/api/auth/change-password",json={"current_password":"wrong-qa-password","new_password":"new-qa-password"})
 assert rejected.status_code==401
 accepted=await client.post("/api/auth/change-password",json={"current_password":"old-qa-password","new_password":"new-qa-password"})
 assert accepted.status_code==200
 assert (await client.get("/api/v2/designs/"+design)).status_code==200
 async with factory() as db:
  user=await db.get(User,owner)
  assert verify_password("new-qa-password",user.password_hash)
  assert (user.terms_version,user.generation_quality_improvement_consent)==old_consent
  assert (await db.get(AuthSession,other_id)).revoked_at is not None
