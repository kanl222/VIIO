"""One-time deployment password rotation; receive both secrets via environment."""
import os
import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo

def main():
    password=os.environ['NEW_POSTGRES_PASSWORD']
    if len(password)<32:raise ValueError('Use a long deployment password')
    user=os.getenv('POSTGRES_USER','postgres')
    with psycopg.connect(make_conninfo(host=os.getenv('POSTGRES_HOST','postgres'),port=5432,
        dbname='postgres',user=user,password=os.getenv('POSTGRES_PASSWORD','postgres')),autocommit=True) as conn:
        conn.execute(sql.SQL('ALTER ROLE {} PASSWORD {}').format(sql.Identifier(user),sql.Literal(password)))
    print('Deployment password rotated; no credentials printed')

if __name__=='__main__':main()
