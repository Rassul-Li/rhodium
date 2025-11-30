#!/usr/bin/env python3
"""
app.py
Flask 3.x + SQLAlchemy 2.x application for the Rhodium DB schema.

Supports:
- Listing items
- Creating items
- Editing items
- UUID-like 16-byte IDs encoded as hex for URL routing
"""

from __future__ import annotations

import os
import binascii
from datetime import datetime, timezone, date, time
from zoneinfo import ZoneInfo
import argparse
import pathlib

from flask import (
	Flask, render_template, request, redirect, url_for, flash, jsonify, g
)
from sqlalchemy import create_engine, select, update
from sqlalchemy.orm import Session

# Import your models
from chaos import (
	Base, Item, SysParameters, Counters,
	make_engine
)

import logging
from logging.handlers import RotatingFileHandler  

# ---------------------------------------------------------------------
#  ID Generation
# ---------------------------------------------------------------------

def generate_item_id(session: Session) -> bytes:
	"""
	Generates a 16-byte monotonic ID with structure:
	  [56-bit timestamp_ms][16-bit mac-hash][56-bit counter]

	All values are stored big-endian and concatenated to 16 bytes.
	"""

	# 1. Monotonic ms timestamp (56 bits)
	ts_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
	ts_bytes = ts_ms.to_bytes(7, "big")   # 56 bits / 7 bytes

	# 2. 16-bit MAC-hash fragment
	row = session.get(SysParameters, "node_id")
	if row is None:
		raise RuntimeError("Database missing SysParameters.node_id")
	node_id_hex = row.value				# e.g. "ab45"
	mac_bytes = bytes.fromhex(node_id_hex) # 2 bytes

	# 3. Increment 56-bit counter
	ctr_row = session.get(Counters, "primary_counter")
	if ctr_row is None:
		raise RuntimeError("Database missing Counters.primary_counter")

	new_value = ctr_row.value + 1
	session.execute(
		update(Counters)
		.where(Counters.key == "primary_counter")
		.values(value=new_value, last_change=datetime.now(timezone.utc))
	)

	ctr_bytes = new_value.to_bytes(7, "big")

	return ts_bytes + mac_bytes + ctr_bytes   # 7 + 2 + 7 = 16 bytes

# ---------------------------------------------------------------------
#  Helpers
# ---------------------------------------------------------------------

def hexid(b: bytes) -> str:
	"""Convert BLOB(16) to readable hex string."""
	return binascii.hexlify(b).decode("ascii")


def unhexid(s: str) -> bytes:
	"""Convert 32-char hex string back to 16 bytes."""
	try:
		b = binascii.unhexlify(s)
		if len(b) != 16:
			raise ValueError
		return b
	except Exception:
		raise ValueError("Invalid item ID")


# ---------------------------------------------------------------------
def flask_setup(db_path: pathlib.Path | None = None) -> Flask:

	if db_path is None:
		db_path = pathlib.Path("/home/rhodium/db/rhodium.db")
	
	db_path.parent.mkdir(parents=True, exist_ok=True)

	app = Flask(__name__)

	app.secret_key = os.getenv("RHODIUM_SECRET")

	if not app.secret_key:
		raise RuntimeError("Environment variable RHODIUM_SECRET must be set")
	
	# Store engine in config
	engine = make_engine(db_path)
	Base.metadata.create_all(engine)
	app.config['SQLALCHEMY_ENGINE'] = engine
	
	# Create session for each request
	@app.before_request
	def before_request():
		g.db = Session(app.config['SQLALCHEMY_ENGINE'])
	
	# Clean up session after each request
	@app.teardown_request
	def teardown_request(exception):
		db = g.pop('db', None)
		if db is not None:
			if exception:
				db.rollback()
			db.close()
	

	@app.route("/")
	def index():
		rows = g.db.execute(
			select(Item).order_by(Item.priority.desc(), Item.due_date.asc().nulls_last())
		).scalars().all()
		return render_template("index.html", items=rows)
	
	@app.route("/create", methods=["GET", "POST"])
	def create():
		if request.method == "POST":
			title = request.form["title"].strip()
			if not title:
				flash("Title is required.", "danger")
				return redirect(url_for("create"))
			
			description = request.form.get("description") or None
			recurring = request.form.get("recurring") or None
			priority = int(request.form.get("priority") or 0)
			due_raw = request.form.get("due_date")
			due_date = datetime.fromisoformat(due_raw) if due_raw else None
			
			item_id = generate_item_id(g.db)
			itm = Item(
				id=item_id,
				title=title,
				description=description,
				due_date=due_date,
				recurring=recurring,
				priority=priority,
			)
			g.db.add(itm)
			g.db.commit()
			
			flash("Item created.", "success")
			return redirect(url_for("index"))

		return render_template("create.html")


	@app.route("/edit/<string:item_hex>", methods=["GET", "POST"])
	def edit(item_hex):
		try:
			item_id = unhexid(item_hex)
		except ValueError:
			return "Invalid ID", 400
		
		item = g.db.get(Item, item_id)
		if item is None:
			return "Item not found", 404
		
		if request.method == "POST":
			title = request.form["title"]
			description = request.form.get("description") or None
			recurring = request.form.get("recurring") or None
			priority = int(request.form.get("priority") or 0)
			status = request.form.get("status") or item.status
			due_raw = request.form.get("due_date")
			due_date = datetime.fromisoformat(due_raw) if due_raw else None
			
			completed_at = item.completed_at
			if status == "done" and item.completed_at is None:
				completed_at = datetime.now(timezone.utc)
			
			g.db.execute(
				update(Item)
				.where(Item.id == item_id)
				.values(
					title=title,
					description=description,
					due_date=due_date,
					recurring=recurring,
					priority=priority,
					status=status,
					completed_at=completed_at,
				)
			)
			g.db.commit()
			flash("Item updated.", "success")
			return redirect(url_for("index"))
		
		return render_template("edit.html", item=item, item_hex=item_hex)


	@app.route("/api/today")
	def api_today():
		tz_name = request.args.get("tz", "UTC")
		
		try:
			user_tz = ZoneInfo(tz_name)
		except Exception:
			return jsonify({"error": f"Invalid timezone: {tz_name}"}), 400
		
		# End of today in user's timezone
		now_user = datetime.now(timezone.utc).astimezone(user_tz)
		today_end = datetime.combine(now_user.date(), time.max, tzinfo=user_tz)
		
		# Get all incomplete items due by end of today (or with no due date)
		rows = g.db.execute(
			select(Item)
			.where(Item.status != "done")
			.where((Item.due_date.is_(None)) | (Item.due_date <= today_end))
			.order_by(Item.priority.desc())
		).scalars().all()
		
		return jsonify([
			{
				"id": hexid(i.id),
				"title": i.title,
				"description": i.description,
				"due_date": i.due_date.isoformat() if i.due_date else None,
				"status": i.status,
			}
			for i in rows
		])
	
	@app.route("/health")
	def health():
		try:
			g.db.execute(select(1))
			return jsonify({"status": "healthy"}), 200
		except Exception as e:
			return jsonify({"status": "unhealthy", "error": str(e)}), 503

	if not app.debug:
		log_dir = db_path.parent / 'logs'
		log_dir.mkdir(exist_ok=True)
		
		handler = RotatingFileHandler(
			log_dir / 'rhodium.log',
			maxBytes=10_000_000,
			backupCount=5
		)
		handler.setFormatter(logging.Formatter(
			'[%(asctime)s] %(levelname)s in %(module)s: %(message)s'
		))
		handler.setLevel(logging.INFO)
		app.logger.addHandler(handler)
		app.logger.setLevel(logging.INFO)
		app.logger.info('Rhodium startup')

	return app

if __name__ == "__main__":
	parser = argparse.ArgumentParser(description="Rhodium hestia runtime")
	parser.add_argument('--path', type=pathlib.Path, default=pathlib.Path("/home/rhodium/db"))
	parser.add_argument('--dev', action='store_true', help='Run in development mode')
	args = parser.parse_args()

	DB_PATH = args.path.joinpath("rhodium.db")
	app = flask_setup(DB_PATH)
	
	if args.dev:
		app.run(host="0.0.0.0", port=80, debug=True)
	else:
		from waitress import serve
		print(f'Serving on http://0.0.0.0:80')
		serve(app, host="0.0.0.0", port=80, threads=4)