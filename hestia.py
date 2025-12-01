#!/usr/bin/env python3
"""
hestia.py
Flask 3.x + SQLAlchemy 2.x application for the Rhodium DB schema.

Supports:
- Listing items
- Creating items
- Editing items
- UUID-like 16-byte IDs encoded as hex for URL routing
"""

from __future__ import annotations

import sys

import argparse
import pathlib

from flask import (
	Flask, g
)
from sqlalchemy import Engine
from sqlalchemy.orm import Session

import chiron
import hermes

import logging
from logging.handlers import WatchedFileHandler  

# ---------------------------------------------------------------------
def flask_setup(db_dir: pathlib.Path, log_dir: pathlib.Path, secret_key: str) -> Flask:

	db_path = db_dir.joinpath("rhodium.db")
	db_path.parent.mkdir(parents=True, exist_ok=True)

	app = Flask(__name__)

	app.secret_key = secret_key

	if not app.secret_key:
		raise RuntimeError("Environment variable RHODIUM_SECRET must be set")
	
	# Store engine in config
	engine = chiron.make_engine(db_path)
	chiron.Base.metadata.create_all(engine)
	app.extensions['sqlalchemy_engine'] = engine
	
	# Create session for each request
	@app.before_request
	def _before_request(): # pyright: ignore[reportUnusedFunction]
		engine: Engine = app.extensions['sqlalchemy_engine']
		g.db = Session(engine)
	
	# Clean up session after each request
	@app.teardown_request
	def _teardown_request(exception: BaseException | None): # pyright: ignore[reportUnusedFunction]
		db = g.pop('db', None)
		if db is not None:
			if exception:
				db.rollback()
			db.close()

	if not app.debug:
		log_dir = pathlib.Path('/var/log/rhodium')
		log_file = log_dir.joinpath('rhodium.log')

		try:
			log_dir.mkdir(parents=True, exist_ok=True)

			# WatchedFileHandler detects when logrotate moves the file
			handler = WatchedFileHandler(log_file)
			handler.setFormatter(logging.Formatter(
				'[%(asctime)s] %(levelname)s in %(module)s: %(message)s'
			))
			handler.setLevel(logging.INFO)
			app.logger.addHandler(handler)
			app.logger.setLevel(logging.INFO)
			app.logger.info('Rhodium startup')
		
		except (PermissionError, OSError) as e:
			print(f"WARNING: Cannot set up file logging: {e}", file=sys.stderr)
			app.logger.setLevel(logging.INFO)
			app.logger.info('Rhodium startup (stderr logging only)')
	
	app.register_blueprint(hermes.bp)

	return app

if __name__ == "__main__":
	parser = argparse.ArgumentParser(description="Rhodium hestia runtime")
	parser.add_argument('--dir', type=pathlib.Path, default=pathlib.Path("/var/lib/rhodium"), help='Run with custom mutable path (optional)')
	parser.add_argument('--log', type=pathlib.Path, default=pathlib.Path("/var/log/rhodium"), help='Run with custom logging path (optional)')
	parser.add_argument('--dev', action='store_true', help='Run in development mode')
	parser.add_argument('--regen', action='store_true', help='Regenerate secrets')
	args = parser.parse_args()

	app = flask_setup(args.dir, args.log, chiron.get_secret(args.dir, args.regen))
	
	if args.dev:
		app.run(host="0.0.0.0", port=80, debug=True)
	else:
		from waitress import serve
		print(f'Serving on http://0.0.0.0:80')
		serve(app, host="0.0.0.0", port=80, threads=4)