import sqlite3

c = sqlite3.connect("ratings.db")
n = c.execute("""
    --DELETE FROM events;
    --DELETE FROM scores;
    DELETE FROM enrichments;
            
""")
c.commit()
print(n, "foreign rows deleted")