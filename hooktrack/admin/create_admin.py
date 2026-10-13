"""Create (or reset the password of) a platform admin from the command line.

    python -m admin.create_admin you@company.com            # prompts for the password
    python -m admin.create_admin you@company.com --reset    # set a new password for an existing admin
"""
import getpass
import re
import sys

from app import auth, db

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def main(argv: list[str]) -> int:
    args = [a for a in argv if not a.startswith("--")]
    reset = "--reset" in argv
    if len(args) != 1 or not EMAIL_RE.match(args[0]):
        print(__doc__)
        return 2
    email = args[0].strip().lower()
    db.init_schema()
    existing = db.one("SELECT id FROM platform_admins WHERE email=%s", (email,))
    if existing and not reset:
        print(f"{email} already exists. Use --reset to set a new password.")
        return 1
    if not existing and reset:
        print(f"No admin {email}.")
        return 1
    pw = getpass.getpass("Password (10+ characters): ")
    if len(pw) < 10:
        print("Too short.")
        return 1
    if pw != getpass.getpass("Again: "):
        print("Passwords differ.")
        return 1
    h = auth.hash_password(pw)
    if existing:
        db.execute("UPDATE platform_admins SET password_hash=%s, disabled=FALSE WHERE id=%s", (h, existing["id"]))
        db.execute("DELETE FROM admin_sessions WHERE admin_id=%s", (existing["id"],))
        print(f"Password updated for {email}.")
    else:
        db.execute("INSERT INTO platform_admins(email, name, password_hash) VALUES (%s,%s,%s)", (email, "Admin", h))
        print(f"Created platform admin {email}.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
