#!/usr/bin/env python3
'''
hermes.py
Routes and Blueprint with Timezone Support
'''
from datetime import datetime, timezone, time
from zoneinfo import ZoneInfo
from typing import overload, TypedDict

from dataclasses import dataclass

from flask import Blueprint, g, jsonify, render_template, request, flash, redirect, url_for
from sqlalchemy import select, update

import chiron

bp = Blueprint('main', __name__)

def get_user_tz() -> tuple[str, ZoneInfo, bool]:
	"""Get user's timezone from request args, default to UTC if invalid/missing"""
	tz_name = request.args.get("tz")
	if not tz_name:
		return 'UTC', ZoneInfo('UTC'), False
	try:
		return tz_name, ZoneInfo(tz_name), True
	except Exception:
		return 'UTC', ZoneInfo('UTC'), False

@overload
def to_user_tz(dt_utc: None, user_tz: ZoneInfo) -> None: ...

@overload
def to_user_tz(dt_utc: datetime, user_tz: ZoneInfo) -> datetime: ...

def to_utc(dt: datetime | None, user_tz: ZoneInfo) -> datetime | None:
	"""Convert a datetime to UTC. If naive, assume it's in user's timezone."""
	if dt is None:
		return None
	if dt.tzinfo is None:
		dt = dt.replace(tzinfo=user_tz)
	return dt.astimezone(timezone.utc)

def to_user_tz(dt_utc: datetime | None, user_tz: ZoneInfo) -> datetime | None:
	"""Convert UTC datetime to user's timezone"""
	if dt_utc is None:
		return None
	if dt_utc.tzinfo is None:
		dt_utc = dt_utc.replace(tzinfo=timezone.utc)
	return dt_utc.astimezone(user_tz)

def format_for_form_input(dt: datetime | None, user_tz: ZoneInfo) -> str | None:
	"""Format datetime for HTML datetime-local input (YYYY-MM-DDTHH:MM)"""
	if dt is None:
		return None
	dt = to_user_tz(dt, user_tz)
	return dt.strftime("%Y-%m-%dT%H:%M")

def get_tz_label(user_tz: ZoneInfo) -> str:
	"""Get timezone label for display (e.g., 'PST', 'EST')"""
	now = datetime.now(user_tz)
	return now.strftime("%Z")

@dataclass
class ItemDisplay:
	"""Item data formatted for display with user timezone"""
	id: bytes
	title: str
	description: str | None
	status: str
	due_date: datetime | None
	created_at: datetime
	completed_at: datetime | None
	recurring: str | None
	priority: int
	@property
	def hex_id(self) -> str:
		"""Get hex representation of ID for URLs"""
		return chiron.hexid(self.id)

def items_with_tz(rows: list[chiron.Item], user_tz: ZoneInfo) -> list[ItemDisplay]:
	"""Convert item due dates to user timezone without mutating original objects."""
	return [
		ItemDisplay(
			id=item.id,
			title=item.title,
			description=item.description,
			status=item.status,
			due_date=to_user_tz(item.due_date, user_tz),
			created_at=to_user_tz(item.created_at, user_tz),
			completed_at=to_user_tz(item.completed_at, user_tz),
			recurring=item.recurring,
			priority=item.priority,
		)
		for item in rows
	]

class ParsedItemForm(TypedDict):
	"""Validated form data for item creation/editing"""
	title: str
	description: str | None
	recurring: str | None
	priority: int
	status: str | None
	due_raw: str | None

def parse_item_form(user_tz: ZoneInfo) -> tuple[ParsedItemForm, datetime | None] | tuple[None, None]:
	"""
	Parse and validate item form data.
	
	Returns:
		Success: (parsed_data, due_date_utc)
		Failure: (None, None) - caller should check flash messages for error
	"""
	title = request.form.get("title", "").strip()
	if not title:
		flash("Title is required.", "danger")
		return None, None
	
	try:
		priority = int(request.form.get("priority") or 0)
	except (ValueError, TypeError):
		flash("Invalid priority value, using default of 0.", "warning")
		priority = 0
	
	# Parse and convert due date
	due_raw = request.form.get("due_date") or None
	try:
		due_date_naive = datetime.fromisoformat(due_raw) if due_raw else None
	except ValueError:
		flash("Invalid date format.", "danger")
		return None, None
	due_date_utc = to_utc(due_date_naive, user_tz)
	
	status = request.form.get("status")
	
	parsed: ParsedItemForm = {
		"title": title,
		"description": request.form.get("description") or None,
		"recurring": request.form.get("recurring") or None,
		"priority": priority,
		"status": status,
		"due_raw": due_raw,
	}
	
	return parsed, due_date_utc

@bp.route("/")
def index():
	tz_param, user_tz, tz_arg_exists = get_user_tz()
	tz_label = get_tz_label(user_tz)
	
	rows = g.db.execute(
		select(chiron.Item).order_by(
			chiron.Item.priority.desc(), 
			chiron.Item.due_date.asc().nulls_last()
		)
	).scalars().all()

	return render_template("index.html", items=items_with_tz(rows, user_tz), tz=tz_param, tz_label=tz_label, tz_arg_exists=tz_arg_exists)

@bp.route("/create", methods=["GET", "POST"])
def create():
	tz_param, user_tz, tz_arg_exists = get_user_tz()
	
	if request.method == "POST":
		parsed, due_date = parse_item_form(user_tz)
		if parsed is None: 
			return redirect(url_for("main.create", tz=tz_param))
		item_id = chiron.generate_item_id(g.db)
		itm = chiron.Item(
			id=item_id,
			title=parsed["title"], 
			description=parsed["description"], 
			due_date=due_date,
			recurring=parsed["recurring"],
			priority=parsed["priority"],
		)
		g.db.add(itm)
		g.db.commit()
		
		flash("Item created.", "success")
		return redirect(url_for("main.index", tz=tz_param))
	
	tz_label = get_tz_label(user_tz)
	# Create empty item object for form rendering
	empty_item = type('Item', (), {'title': '', 'description': '', 'priority': 0, 'status': 'todo', 'recurring': ''})()
	return render_template("create.html", item=empty_item, tz=tz_param, tz_label=tz_label, tz_arg_exists=tz_arg_exists)

@bp.route("/edit/<string:item_hex>", methods=["GET", "POST"])
def edit(item_hex: str):
	tz_param, user_tz, tz_arg_exists = get_user_tz()
	try:
		item_id = chiron.unhexid(item_hex)
	except ValueError:
		return "Invalid ID", 400
	
	item = g.db.get(chiron.Item, item_id)
	if item is None:
		return "Item not found", 404
	
	if request.method == "POST":
		parsed, due_date = parse_item_form(user_tz)
		if parsed is None:
			return redirect(url_for("main.edit", tz=tz_param)) 
		completed_at = item.completed_at
		if parsed["status"] == "done" and item.completed_at is None:
			completed_at = datetime.now(timezone.utc)
		
		g.db.execute(
			update(chiron.Item)
			.where(chiron.Item.id == item_id)
			.values(
				title=parsed["title"],
				description=parsed["description"],
				due_date=due_date,
				recurring=parsed["recurring"],
				priority=parsed["priority"],
				status=parsed["status"],
				completed_at=completed_at,
			)
		)
		g.db.commit()
		flash("Item updated.", "success")
		return redirect(url_for("main.index", tz=tz_param))
	
	due_date_formatted = format_for_form_input(item.due_date, user_tz)
	tz_label = get_tz_label(user_tz)
	
	return render_template("edit.html", item=item, item_hex=item_hex, due_date_formatted=due_date_formatted, tz=tz_param, tz_label=tz_label, tz_arg_exists=tz_arg_exists)

@bp.route("/api/today")
def api_today():
	_, user_tz, tz_arg_exists = get_user_tz()
	
	if not tz_arg_exists:
		return jsonify({"error": "Timezone parameter (tz) is required"}), 400
	
	# End of today in user's timezone
	now_user = datetime.now(timezone.utc).astimezone(user_tz)
	today_end = datetime.combine(now_user.date(), time.max, tzinfo=user_tz).astimezone(timezone.utc)
	
	# Get all incomplete items due by end of today (or with no due date)
	rows = g.db.execute(
		select(chiron.Item)
		.where(chiron.Item.status != "done")
		.where((chiron.Item.due_date.is_(None)) | (chiron.Item.due_date <= today_end))
		.order_by(chiron.Item.priority.desc())
	).scalars().all()
	
	# Prepare items based on timezone
	items_to_return = items_with_tz(rows, user_tz)
	
	return jsonify({
		"metadata": {
			"user_tz": str(user_tz),
			"current_time": now_user.isoformat(),
			"iso_weekday": now_user.isoweekday(),  # 1=Monday, 7=Sunday
			"date": now_user.date().isoformat()
		},
		"items": [
			{
				"id": chiron.hexid(i.id),
				"title": i.title,
				"description": i.description,
				"due_date": i.due_date.isoformat() if i.due_date else None,
				"status": i.status,
				"priority": i.priority,
			}
			for i in items_to_return
		]
	})


@bp.route("/health")
def health():
	try:
		g.db.execute(select(1))
		return jsonify({"status": "healthy"}), 200
	except Exception as e:
		return jsonify({"status": "unhealthy", "error": str(e)}), 503