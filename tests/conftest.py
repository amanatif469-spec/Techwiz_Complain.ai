"""Point the app at a disposable database before anything imports the engine.

Integration tests run only when SUPPORTNOVA_TEST_DATABASE_URL is set, e.g.
postgresql+psycopg://supportnova:supportnova@localhost:5432/supportnova_test
The database is dropped and recreated, so its name must contain "test" or "scratch".
"""

import os

TEST_DATABASE_URL = os.environ.get("SUPPORTNOVA_TEST_DATABASE_URL", "")
if TEST_DATABASE_URL:
    db_name = TEST_DATABASE_URL.rsplit("/", 1)[-1].lower()
    if "test" not in db_name and "scratch" not in db_name:
        raise RuntimeError("SUPPORTNOVA_TEST_DATABASE_URL must name a disposable *_test or *_scratch database.")
    os.environ["DATABASE_URL"] = TEST_DATABASE_URL
