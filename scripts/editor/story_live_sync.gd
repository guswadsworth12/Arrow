extends Node
## Live chapter: publishes local edits a moment after they happen and loads everyone
## else's, including AI agents', as they land. The service merges edits that touch
## different nodes and fields; only a true clash is refused.
const Client = preload("story_service_client.gd")
const PUSH_DELAY = 0.6 # seconds of quiet after an edit before publishing it
const CHECK_INTERVAL = 0.3
const WAIT = 20 # seconds the service holds a head request open

signal revision_changed(revision: int)

var Main
var story_id = ""
var chapter = ""
var project_uid = -1
var revision = 0
var active = false
var _push_client
var _pull_client
var _indicator: Label
var _synced = "" # local chapter text when it last matched a revision
var _last_save = null # the synced source's meta.last_save; local saves never sync
var _pending = "" # an unpublished local state, waiting for PUSH_DELAY
var _changed_at = 0.0
var _refused = "" # a local state the service refused, not resent until edited again
var _discard_local = false # after a clash, the next pull replaces local edits
var _pushing = false
var _pulling = false
var _remote_pending = false
var _checking = false # head diagnostics still pending: ask again soon
var _elapsed = 0.0
var _generation = 0 # ends waits that belong to an earlier start
var _notice = "" # a clash or refusal, kept beside the status for NOTICE_SECONDS
var _notice_until = 0.0
const NOTICE_SECONDS = 10.0

func _ready() -> void:
	var layer = CanvasLayer.new()
	layer.layer = 100
	add_child(layer)
	_indicator = Label.new()
	_indicator.set_anchors_and_offsets_preset(Control.PRESET_BOTTOM_RIGHT)
	_indicator.grow_horizontal = Control.GROW_DIRECTION_BEGIN
	_indicator.grow_vertical = Control.GROW_DIRECTION_BEGIN
	_indicator.offset_right = -14
	_indicator.offset_bottom = -10
	_indicator.mouse_filter = Control.MOUSE_FILTER_IGNORE
	_indicator.add_theme_color_override("font_outline_color", Color.BLACK)
	_indicator.add_theme_constant_override("outline_size", 4)
	_indicator.visible = false
	layer.add_child(_indicator)

## Follows the chapter open as project `uid`. When `matches_base`, that project is exactly
## `base_revision`; otherwise its local edits are published against it and merged.
func start(source, story: String, chapter_key: String, uid: int, base_revision: int, matches_base: bool) -> void:
	stop()
	story_id = story
	chapter = chapter_key
	project_uid = uid
	revision = base_revision
	_push_client = _client_like(source)
	_pull_client = _client_like(source)
	_last_save = Main.Mind._PROJECT.meta.get("last_save")
	_synced = _text(Main.Mind._PROJECT) if matches_base else ""
	_pending = ""
	_refused = ""
	_discard_local = false
	_remote_pending = false
	active = true
	_show("Live · %s · revision %s" % [chapter, revision])
	_watch(_generation)

func stop() -> void:
	active = false
	_generation += 1
	for each in [_push_client, _pull_client]:
		if is_instance_valid(each):
			each.queue_free()
	if _indicator != null:
		_indicator.visible = false

func _client_like(source) -> Node:
	var copy = Client.new()
	add_child(copy)
	copy.base_url = source.base_url
	copy.bearer = source.bearer
	copy.access_headers = source.access_headers
	return copy

func _path() -> String:
	return "/v1/stories/" + story_id.uri_encode()

func _editing_this() -> bool:
	return Main.Mind.ProMan.get_active_project_id() == project_uid and Main.Mind._SNAPSHOT_INDEX_OF_PREVIEW < 0

## Typing in a field or dragging on the grid: a reload now would discard it.
func _busy_editing() -> bool:
	var focus = get_viewport().gui_get_focus_owner()
	return focus is LineEdit or focus is TextEdit or Input.is_mouse_button_pressed(MOUSE_BUTTON_LEFT)

func _text(project: Dictionary) -> String:
	var copy = project.duplicate(true)
	copy.meta.erase("last_save")
	return JSON.stringify(copy)

func _document() -> Dictionary:
	var copy = Main.Mind._PROJECT.duplicate(true)
	if _last_save == null:
		copy.meta.erase("last_save")
	else:
		copy.meta.last_save = _last_save
	return copy

func _process(delta: float) -> void:
	_elapsed += delta
	if not active or _elapsed < CHECK_INTERVAL:
		return
	_elapsed = 0.0
	if not _editing_this():
		_show("Live paused · open %s to keep syncing" % chapter)
		return
	var text = _text(Main.Mind._PROJECT)
	var now = Time.get_ticks_msec() / 1000.0
	if text != _synced and text != _refused:
		if text != _pending:
			_pending = text
			_changed_at = now
		elif now - _changed_at >= PUSH_DELAY and not _pushing and not _pulling:
			_push(text)
	elif _remote_pending and not _pushing and not _pulling and not _busy_editing():
		_pull()

func _push(text: String) -> void:
	_pushing = true
	var generation = _generation
	var response = await _push_client.request(HTTPClient.METHOD_POST, _path() + "/commits", {
		"base_revision": revision, "message": "Edited in Arrow",
		"operations": [{"op": "replace_document", "document": chapter, "value": _document()}]})
	_pushing = false
	if generation != _generation:
		return
	if response.ok:
		_refused = ""
		var at = int(response.value.revision)
		var result = response.value.bundle.documents[chapter]
		var converted = Main.Mind.ProMan.read_browsed_project_content(JSON.stringify(result))
		if converted is Dictionary and _text(converted) == text:
			_synced = text
			_set_revision(at)
		elif _text(Main.Mind._PROJECT) == text:
			# Merged with someone else's edits: show the combined chapter.
			_load(result, at)
		# Otherwise keep the old base: the next publish merges again, then loads.
		return
	var error = response.value.get("error", {})
	if response.status == 409 and error.get("code") == "edit_conflict":
		_warn("Your last change clashed with another edit (%s); kept theirs" % ", ".join(error.get("conflicts", [])))
		_refused = text
		_discard_local = true
		_remote_pending = true
	elif response.status == 0 or response.status >= 500:
		_show("Live · offline, retrying: " + str(error.get("message", "")))
		_pending = ""
	else:
		_warn("This change was refused: " + str(error.get("message", "HTTP %s" % response.status)))
		_refused = text

func _pull() -> void:
	_pulling = true
	var generation = _generation
	var response = await _push_client.request(HTTPClient.METHOD_GET, _path())
	_pulling = false
	if generation != _generation or not response.ok:
		return
	# Local edits made meanwhile publish first; the service merges them, then this repeats.
	if _discard_local or _text(Main.Mind._PROJECT) == _synced:
		_load(response.value.bundle.documents[chapter], int(response.value.revision))

func _watch(generation: int) -> void:
	while generation == _generation:
		if _remote_pending:
			await get_tree().create_timer(CHECK_INTERVAL).timeout
			continue
		var wait = 2 if _checking else WAIT
		var response = await _pull_client.request(HTTPClient.METHOD_GET, _path() + "/head?after=%d&wait=%d" % [revision, wait])
		if generation != _generation:
			return
		if not response.ok:
			_show("Live · reconnecting (HTTP %s)" % response.status)
			await get_tree().create_timer(3.0).timeout
			continue
		if int(response.value.revision) > revision:
			_remote_pending = true
		else:
			_report(response.value)

func _load(document: Dictionary, at: int) -> bool:
	var data = Main.Mind.ProMan.read_browsed_project_content(JSON.stringify(document))
	if not data is Dictionary:
		_show("Live · Arrow could not read revision %s" % at)
		return false
	var mind = Main.Mind
	var scene = mind._CURRENT_OPEN_SCENE_ID
	var inspected = mind.Inspector.Tab.Node._CURRENT_INSPECTED_NODE_RESOURCE_ID
	_last_save = data.meta.get("last_save")
	mind.load_full_project_image({"project": data, "view": mind.get_current_view_state(),
		"open_scene": scene if data.resources.scenes.has(scene) else -1})
	# Undo must not step back across someone else's edit and quietly revert it.
	mind.forget_history()
	mind.history_check_point()
	if inspected >= 0 and data.resources.nodes.has(inspected):
		mind.call_deferred("inspect_node", inspected)
	_synced = _text(mind._PROJECT)
	_pending = ""
	_discard_local = false
	_remote_pending = false
	_set_revision(at)
	return true

func _set_revision(at: int) -> void:
	if at != revision:
		revision = at
		revision_changed.emit(at)
	_checking = true
	_show("Live · %s · revision %s · checking…" % [chapter, revision])

func _report(head: Dictionary) -> void:
	var validation = head.get("validation", {})
	var text = "Live · %s · revision %s" % [chapter, revision]
	_checking = validation.get("pending", false)
	if _checking:
		text += " · checking…"
	elif validation.get("ok") == true:
		text += " · compiles"
	elif validation.get("errors", []).size() > 0:
		text += " · %s compiler issue(s): %s" % [validation.errors.size(), validation.errors[0]]
	_show(text)

func _warn(text: String) -> void:
	_notice = text
	_notice_until = Time.get_ticks_msec() / 1000.0 + NOTICE_SECONDS
	_show("Live · " + chapter)

func _show(text: String) -> void:
	if _indicator == null:
		return
	if not _notice.is_empty() and Time.get_ticks_msec() / 1000.0 < _notice_until:
		text += "\n" + _notice
	_indicator.text = text
	_indicator.visible = active
