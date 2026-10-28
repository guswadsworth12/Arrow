extends Window
## Shared story review UI; Arrow project saves stay local until an explicit apply.
const Client = preload("story_service_client.gd")
const BINDINGS_PATH = "user://story-service-bindings.json"
var Main
var client
var server: LineEdit
var token: LineEdit
var stories: OptionButton
var chapters: OptionButton
var message: LineEdit
var proposals: ItemList
var review: TextEdit
var status: Label
var apply_button: Button
var next_button: Button
var controls: Array[Control] = []
var snapshot: Dictionary = {}
var reviewed: Dictionary = {}
var bindings: Dictionary = {}
var cursor = 0
var last_cursor = 0
var working = false
var import_picker: FileDialog
var story_settings: Window

func _ready() -> void:
	title = "Arrow · Story service"
	initial_position = Window.WINDOW_INITIAL_POSITION_CENTER_MAIN_WINDOW_SCREEN
	size = Vector2i(1040, 730)
	min_size = Vector2i(760, 580)
	close_requested.connect(hide)
	client = Client.new()
	add_child(client)
	var margin = MarginContainer.new()
	margin.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	for side in ["left", "right", "top", "bottom"]:
		margin.add_theme_constant_override("margin_" + side, 14)
	add_child(margin)
	var layout = VBoxContainer.new()
	margin.add_child(layout)
	var connection = _row(layout)
	server = _field(connection, "Service URL", OS.get_environment("ARROW_STORY_URL"))
	if server.text.is_empty():
		server.text = "http://127.0.0.1:8765"
	token = _field(connection, "Bearer token (session only)", OS.get_environment("ARROW_STORY_API_TOKEN"))
	token.secret = true
	_button(connection, "Connect", connect_service)
	var story_row = _row(layout)
	stories = OptionButton.new()
	stories.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	story_row.add_child(stories)
	controls.append(stories)
	stories.item_selected.connect(_select_story)
	_button(story_row, "Load story", load_story)
	_button(story_row, "Import manifest…", func(): import_picker.popup_centered_ratio(0.7))
	var chapter_row = _row(layout)
	chapters = OptionButton.new()
	chapters.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	chapter_row.add_child(chapters)
	controls.append(chapters)
	_button(chapter_row, "Open chapter in Arrow", open_chapter)
	_button(chapter_row, "Shared story settings…", open_story_settings)
	message = _field(_row(layout), "Review message for import or editor changes", "")
	var actions = _row(layout)
	_button(actions, "Propose current Arrow chapter", propose_current)
	_button(actions, "Refresh proposals", refresh_proposals)
	next_button = _button(actions, "Next page", next_page)
	var split = HSplitContainer.new()
	split.size_flags_vertical = Control.SIZE_EXPAND_FILL
	layout.add_child(split)
	proposals = ItemList.new()
	proposals.custom_minimum_size.x = 285
	proposals.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	proposals.size_flags_stretch_ratio = 0.65
	split.add_child(proposals)
	controls.append(proposals)
	proposals.item_selected.connect(func(_index): _clear_review())
	proposals.item_activated.connect(func(_index): review_selected())
	review = TextEdit.new()
	review.editable = false
	review.add_theme_color_override("font_readonly_color", Color(0.94, 0.94, 0.94))
	review.size_flags_stretch_ratio = 1.35
	review.wrap_mode = TextEdit.LINE_WRAPPING_BOUNDARY
	review.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	split.add_child(review)
	var bottom = _row(layout)
	_button(bottom, "Review selected", review_selected)
	apply_button = _button(bottom, "Apply reviewed proposal", apply_reviewed)
	_button(bottom, "Close", hide)
	status = Label.new()
	status.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	layout.add_child(status)
	status.text = "Connect to load stories. Proposals do not change the shared revision until applied."
	import_picker = FileDialog.new()
	import_picker.access = FileDialog.ACCESS_FILESYSTEM
	import_picker.file_mode = FileDialog.FILE_MODE_OPEN_FILE
	import_picker.filters = PackedStringArray(["*.json ; Arrow story manifest"])
	add_child(import_picker)
	import_picker.file_selected.connect(import_manifest)
	server.text_changed.connect(func(_text): _connection_changed())
	token.text_changed.connect(func(_text): _connection_changed())
	var saved = Client.read_json(BINDINGS_PATH)
	if saved.ok:
		bindings = saved.value
	_set_working(false)

func _row(parent: Node) -> HBoxContainer:
	var row = HBoxContainer.new()
	parent.add_child(row)
	return row

func _field(parent: Node, placeholder: String, value: String) -> LineEdit:
	var field = LineEdit.new()
	field.placeholder_text = placeholder
	field.text = value
	field.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	parent.add_child(field)
	controls.append(field)
	return field

func _button(parent: Node, text: String, action: Callable) -> Button:
	var button = Button.new()
	button.text = text
	button.pressed.connect(action)
	parent.add_child(button)
	controls.append(button)
	return button

func _set_working(value: bool) -> void:
	working = value
	for control in controls:
		if control is LineEdit:
			control.editable = not value
		elif control is BaseButton:
			control.disabled = value
		elif control is ItemList:
			control.mouse_filter = Control.MOUSE_FILTER_IGNORE if value else Control.MOUSE_FILTER_STOP
	apply_button.disabled = value or reviewed.get("status") != "ready"
	next_button.disabled = value or last_cursor <= cursor

func _clear_review() -> void:
	reviewed = {}
	review.text = "Select a proposal and choose Review selected to see its diff and compiler diagnostics."
	if apply_button != null:
		apply_button.disabled = true

func _connection_changed() -> void:
	snapshot = {}
	stories.clear()
	chapters.clear()
	proposals.clear()
	_clear_review()
	client.base_url = ""
	client.bearer = ""
	status.text = "Connection changed. Connect again. Local chapter bindings retain their original service and revision."

func _configure() -> bool:
	if not client.configure(server.text, token.text):
		status.text = "Use HTTPS (or HTTP on loopback) and a printable bearer token of at least 32 characters."
		return false
	return true

func _story() -> String:
	return "" if stories.selected < 0 else str(stories.get_item_metadata(stories.selected))

func _path(story_id: String) -> String:
	return "/v1/stories/" + story_id.uri_encode()

func _send(method: HTTPClient.Method, path: String, body: Variant = null) -> Dictionary:
	_set_working(true)
	status.text = "Waiting for story service…"
	var response = await client.request(method, path, body)
	_set_working(false)
	if not response.ok:
		_clear_review()
		review.text = JSON.stringify(response.value, "  ")
		status.text = "Service request failed (HTTP %s). See details above. Local edits and their base revision are retained." % response.status
	return response

func connect_service() -> void:
	if working or not _configure():
		return
	var response = await _send(HTTPClient.METHOD_GET, "/v1/stories")
	if response.ok:
		snapshot = {}
		chapters.clear()
		proposals.clear()
		_clear_review()
		stories.clear()
		for item in response.value.get("stories", []):
			stories.add_item("%s · revision %s" % [item.id, int(item.head)])
			stories.set_item_metadata(stories.item_count - 1, item.id)
		status.text = "Connected. Choose a story and load it, or import a story manifest."

func load_story() -> void:
	if working or _story().is_empty() or not _configure():
		return
	var response = await _send(HTTPClient.METHOD_GET, _path(_story()))
	if response.ok:
		snapshot = response.value
		chapters.clear()
		for chapter in snapshot.bundle.documents:
			chapters.add_item(chapter)
		_clear_review()
		status.text = "Loaded %s revision %s. Opening a chapter creates a separate local Arrow project." % [snapshot.story_id, int(snapshot.revision)]

func open_chapter() -> void:
	if working or snapshot.is_empty() or chapters.selected < 0:
		return
	if not Main.Mind.ProMan.is_project_saved():
		status.text = "Save or close the current Arrow project before opening a service chapter."
		return
	var chapter = chapters.get_item_text(chapters.selected)
	var data = Main.Mind.ProMan.read_browsed_project_content(JSON.stringify(snapshot.bundle.documents[chapter]))
	if not data is Dictionary:
		status.text = "Arrow could not open this chapter. No local project was replaced."
		return
	var filename = Main.Mind.ProMan.valid_unique_project_filename_from("service_%s_%s_r%s" % [snapshot.story_id, chapter, snapshot.revision])
	var uid = Main.Mind.ProMan.register_project(data.title, filename, false)
	if Main.Mind.ProMan.save_project_into(uid, data, false, true) != OK:
		status.text = "Could not save the new local chapter. Check Arrow's local storage path."
		return
	Main.Mind.open_project(uid)
	if Main.Mind.ProMan.get_active_project_id() != uid:
		status.text = "Could not open the local chapter."
		return
	bindings[str(uid)] = {"service": client.base_url, "story_id": snapshot.story_id, "chapter": chapter, "revision": int(snapshot.revision), "filename": filename, "project_path": ProjectSettings.globalize_path(Main.Mind.ProMan.get_project_file_path(uid))}
	var persisted = _save_bindings()
	status.text = "Editing %s at base revision %s. Use Arrow normally, then return here to propose this chapter." % [chapter, int(snapshot.revision)]
	if not persisted:
		status.text += " Binding could not be saved; keep this session open and save a local copy."
	else:
		hide()

func _save_bindings() -> bool:
	var file = FileAccess.open(BINDINGS_PATH + ".tmp", FileAccess.WRITE)
	if file != null:
		file.store_string(JSON.stringify(bindings))
		file.close()
		if DirAccess.rename_absolute(BINDINGS_PATH + ".tmp", BINDINGS_PATH) == OK:
			return true
	return false

func propose_current() -> void:
	if working or not _configure():
		return
	var uid = Main.Mind.ProMan.get_active_project_id()
	var binding = bindings.get(str(uid), {})
	var listing = Main.Mind.ProMan.get_project_listing_by_id(uid)
	if not binding is Dictionary or binding.is_empty() or not listing is Dictionary or listing.filename != binding.get("filename") or ProjectSettings.globalize_path(Main.Mind.ProMan.get_project_file_path(uid)) != binding.get("project_path"):
		status.text = "Open a service chapter from this window first. The current project has no service binding."
		return
	if binding.service != client.base_url:
		status.text = "This chapter belongs to another service. Reconnect to its original URL."
		return
	if message.text.strip_edges().is_empty():
		status.text = "Enter a review message before proposing edits."
		return
	if Main.Mind._SNAPSHOT_INDEX_OF_PREVIEW >= 0:
		status.text = "Return to the editable project before proposing a snapshot preview."
		return
	var response = await _send(HTTPClient.METHOD_POST, _path(binding.story_id) + "/proposals", {
		"base_revision": int(binding.revision), "message": message.text,
		"operations": [{"op": "replace_document", "document": binding.chapter, "value": Main.Mind.capture_full_project_image("service_proposal").project}]})
	if response.ok:
		_show_review(response.value)
		status.text = "Proposal saved (%s). Review the diff and diagnostics before applying. Base revision stays %s." % [reviewed.status, binding.revision]

func refresh_proposals() -> void:
	cursor = 0
	await _load_proposals()

func next_page() -> void:
	if last_cursor > cursor:
		cursor = last_cursor
		await _load_proposals()

func _load_proposals() -> void:
	if working or _story().is_empty() or not _configure():
		return
	var response = await _send(HTTPClient.METHOD_GET, _path(_story()) + "/proposals?after=%s&limit=50" % cursor)
	if response.ok:
		proposals.clear()
		_clear_review()
		last_cursor = cursor
		for item in response.value.get("proposals", []):
			proposals.add_item("%s · r%s · %s" % [item.status, int(item.base_revision), item.message])
			proposals.set_item_tooltip(proposals.item_count - 1, "%s\n%s" % [item.id, item.message])
			proposals.set_item_metadata(proposals.item_count - 1, item.id)
			last_cursor = int(item.sequence)
		_set_working(false)
		status.text = "Proposal page after %s. Double-click a proposal to review it. Refresh starts from the first page." % cursor

func review_selected() -> void:
	if working or proposals.get_selected_items().is_empty() or not _configure():
		return
	var id = str(proposals.get_item_metadata(proposals.get_selected_items()[0]))
	var response = await _send(HTTPClient.METHOD_GET, _path(_story()) + "/proposals/" + id.uri_encode())
	if response.ok:
		_show_review(response.value)
		status.text = "Reviewed %s proposal. Apply rechecks the current revision and compiler." % reviewed.status

func _show_review(value: Dictionary) -> void:
	reviewed = value
	var lines: Array[String] = [
		"%s · %s" % [str(value.status).to_upper(), value.id],
		"Story: %s" % value.story_id,
		"Base revision %s · current revision %s" % [int(value.base_revision), int(value.current_revision)],
		"Message: " + str(value.message), "",
		"Changes (%s)" % value.diff.size(), ""]
	for change in value.diff:
		lines.append(str(change.path))
		lines.append("Before: " + (JSON.stringify(change.before, "  ") if change.before_exists else "(absent)"))
		lines.append("After: " + (JSON.stringify(change.after, "  ") if change.after_exists else "(absent)"))
		lines.append("")
	lines.append("Compiler: " + ("passed" if value.validation.ok else "failed"))
	for diagnostic in value.validation.get("errors", []):
		lines.append(str(diagnostic))
	lines.append("Godot: " + str(value.validation.get("godot", "see service validation record")))
	lines.append("Compiler fingerprint: " + str(value.validation.get("fingerprint", "unavailable")))
	review.text = "\n".join(lines)
	_set_working(false)

func apply_reviewed() -> void:
	if working or reviewed.get("status") != "ready" or not _configure():
		return
	var response = await _send(HTTPClient.METHOD_POST, _path(reviewed.story_id) + "/proposals/" + str(reviewed.id).uri_encode() + "/apply", {})
	if response.ok:
		_clear_review()
		status.text = "Applied revision %s. Local chapters keep their original base. Load the story and open a fresh chapter to continue." % response.value.revision
		review.text = "Committed %s revision %s\nSHA-256: %s" % [response.value.story_id, int(response.value.revision), response.value.sha256]

func import_manifest(path: String) -> void:
	if working or not _configure():
		return
	if message.text.strip_edges().is_empty():
		status.text = "Enter a review message before importing a manifest."
		return
	var bundle = Client.read_bundle(path)
	if not bundle.ok:
		status.text = bundle.error
		return
	var response = await _send(HTTPClient.METHOD_POST, "/v1/stories", {"bundle": bundle.value, "message": message.text})
	if response.ok:
		await connect_service()
		for index in stories.item_count:
			if stories.get_item_metadata(index) == response.value.story_id:
				stories.select(index)
		await load_story()

func _select_story(_index: int) -> void:
	_clear_review()
	snapshot = {}
	chapters.clear()
	proposals.clear()
	cursor = 0
	last_cursor = 0
	_set_working(false)

func open_story_settings() -> void:
	if working or snapshot.is_empty():
		status.text = "Load a story before opening shared story settings."
		return
	if not _configure():
		return
	if not is_instance_valid(story_settings):
		story_settings = preload("story_settings_window.gd").new()
		story_settings.service_window = self
		add_child(story_settings)
	story_settings.begin(snapshot, client.base_url)
