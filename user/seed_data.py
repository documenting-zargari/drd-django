"""
Canonical list of default users for the RMS project.

Shared by the ``seed_users`` and ``setup_auth`` management commands so the
roster lives in exactly one place.
"""

SEED_USERS = [
    {
        "username": "mundstein",
        "email": "smundstein@gmail.com",
        "first_name": "Sascha",
        "last_name": "Mundstein",
        "is_global_admin": True,
        "project_roles": [{"project": "rlb", "role": "admin"}],
    },
    {
        "username": "wiedner",
        "email": "jakob.wiedner@uni-graz.ac.at",
        "first_name": "Jakob",
        "last_name": "Wiedner",
        "is_global_admin": False,
        "project_roles": [{"project": "rlb", "role": "editor"}],
    },
    {
        "username": "aminian",
        "email": "Ioana.Aminian@oeaw.ac.at",
        "first_name": "Ioana",
        "last_name": "Aminian-Jazi",
        "is_global_admin": False,
        "project_roles": [{"project": "rlb", "role": "editor"}],
    },
    {
        "username": "yaron",
        "email": "y.matras@aston.ac.uk",
        "first_name": "Yaron",
        "last_name": "Matras",
        "is_global_admin": False,
        "project_roles": [{"project": "rlb", "role": "admin"}],
    },
]
