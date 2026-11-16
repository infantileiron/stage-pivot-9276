import hashlib,hmac,os,time,jwt
KEY=os.environ['JWT_SECRET']
def hash_password(p):
 salt=os.urandom(16).hex();return salt+':'+hashlib.pbkdf2_hmac('sha256',p.encode(),salt.encode(),160000).hex()
def verify(p,h):
 salt,digest=h.split(':');return hmac.compare_digest(hashlib.pbkdf2_hmac('sha256',p.encode(),salt.encode(),160000).hex(),digest)
def token(u):return jwt.encode({'sub':str(u.id),'exp':int(time.time())+28800},KEY,algorithm='HS256')
