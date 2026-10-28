extends Window
## Visual shared definitions and Jump routing, submitted through the existing service.
const Draft = preload("story_settings_draft.gd")
var service_window
var draft = Draft.new()
var service_url = ""
var controls: Array[Control] = []
var working = false
var heading: Label
var status: Label
var character: OptionButton
var character_name: LineEdit
var character_usage: Label
var variable: OptionButton
var variable_type: Label
var variable_usage: Label
var default_bool: CheckBox
var default_text: LineEdit
var source_chapter: OptionButton
var source_jump: OptionButton
var target_chapter: OptionButton
var target_node: OptionButton
var local_target: OptionButton
var current_link: Label
var pending: ItemList
var pending_count: Label
var message: LineEdit
var tabs: TabContainer
var overview: TextEdit

func _ready() -> void:
	title = "Arrow · Shared story settings"
	size = Vector2i(1000, 720)
	min_size = Vector2i(800, 620)
	close_requested.connect(hide)
	var margin = MarginContainer.new()
	margin.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	for side in ["left", "right", "top", "bottom"]:
		margin.add_theme_constant_override("margin_" + side, 14)
	add_child(margin)
	var layout = VBoxContainer.new()
	margin.add_child(layout)
	heading = _label(layout, "Load a story in the service window first.")
	tabs = TabContainer.new()
	tabs.size_flags_vertical = Control.SIZE_EXPAND_FILL
	layout.add_child(tabs)
	var chapters = _tab("Chapters")
	overview = TextEdit.new()
	overview.editable = false
	overview.add_theme_color_override("font_readonly_color", Color(0.94, 0.94, 0.94))
	overview.wrap_mode = TextEdit.LINE_WRAPPING_BOUNDARY
	overview.size_flags_vertical = Control.SIZE_EXPAND_FILL
	chapters.add_child(overview)
	var characters = _tab("Shared characters")
	_label(characters, "Change a character's display name in every mapped chapter together.")
	character = _choice(characters, "Character")
	character.item_selected.connect(func(_index): _refresh_character())
	character_name = _field(characters, "Display name")
	character_usage = _label(characters, "")
	_button(characters, "Stage character name", stage_character)
	var variables = _tab("Shared variables")
	_label(variables, "Defaults are authored starting values. They do not change a running game or existing saves.")
	variable = _choice(variables, "Variable")
	variable.item_selected.connect(func(_index): _refresh_variable())
	variable_type = _label(variables, "")
	default_bool = CheckBox.new()
	default_bool.text = "Starting value is true"
	variables.add_child(default_bool)
	controls.append(default_bool)
	default_text = _field(variables, "Starting value")
	variable_usage = _label(variables, "")
	_button(variables, "Stage variable default", stage_variable)
	var links = _tab("Chapter links")
	_label(links, "Route an existing Jump to another chapter, or give it a local destination. No nodes are created.")
	source_chapter = _choice(links, "Source chapter")
	source_chapter.item_selected.connect(func(_index): _refresh_jumps())
	source_jump = _choice(links, "Jump node")
	source_jump.item_selected.connect(func(_index): _refresh_link())
	current_link = _label(links, "")
	var destinations = HBoxContainer.new()
	links.add_child(destinations)
	var external = VBoxContainer.new()
	external.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	destinations.add_child(external)
	target_chapter = _choice(external, "Linked chapter")
	target_chapter.item_selected.connect(func(_index): _fill_nodes(target_node, _selected(target_chapter)))
	target_node = _choice(external, "Destination node")
	_button(external, "Stage chapter link", stage_link)
	var local = VBoxContainer.new()
	local.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	destinations.add_child(local)
	local_target = _choice(local, "Local destination instead")
	_button(local, "Stage local jump", stage_local_jump)
	_label(local, "This removes any chapter link and sets a local Jump target in one proposal.")
	var staged = _tab("Staged changes")
	pending_count = _label(staged, "No staged changes.")
	pending = ItemList.new()
	pending.size_flags_vertical = Control.SIZE_EXPAND_FILL
	staged.add_child(pending)
	controls.append(pending)
	var actions = HBoxContainer.new()
	staged.add_child(actions)
	_button(actions, "Remove selected", remove_selected)
	_button(actions, "Clear staged changes", clear_staged)
	_label(layout, "Changes stay in this session until proposed. Closing this window keeps the staged batch.")
	message = _field(layout, "Review message for this batch")
	message.max_length = 512
	var bottom = HBoxContainer.new()
	layout.add_child(bottom)
	_button(bottom, "Propose staged changes", propose)
	_button(bottom, "Close", hide)
	status = _label(layout, "Stage changes, then propose them for compiler validation and review.")

func _tab(label: String) -> VBoxContainer:
	var box = VBoxContainer.new()
	box.name = label
	box.add_theme_constant_override("separation", 10)
	tabs.add_child(box)
	return box

func _label(parent: Node, text: String) -> Label:
	var label = Label.new()
	label.text = text
	label.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	parent.add_child(label)
	return label

func _field(parent: Node, hint: String) -> LineEdit:
	var field = LineEdit.new()
	field.placeholder_text = hint
	parent.add_child(field)
	controls.append(field)
	return field

func _choice(parent: Node, label: String) -> OptionButton:
	_label(parent, label)
	var choice = OptionButton.new()
	choice.fit_to_longest_item = false
	choice.clip_text = true
	parent.add_child(choice)
	controls.append(choice)
	return choice

func _button(parent: Node, label: String, callback: Callable) -> void:
	var button = Button.new()
	button.text = label
	button.pressed.connect(callback)
	parent.add_child(button)
	controls.append(button)

func _selected(choice: OptionButton) -> String:
	return "" if choice.selected < 0 else str(choice.get_item_metadata(choice.selected))

func _select(choice: OptionButton, value: String) -> void:
	for index in choice.item_count:
		if choice.get_item_metadata(index) == value:
			choice.select(index)
			return

func begin(snapshot: Dictionary, url: String) -> bool:
	if working:
		return false
	if not draft.groups.is_empty():
		status.text = "This batch still has staged edits at revision %s. Propose or clear them before loading another snapshot." % int(draft.source.revision)
		popup_centered()
		return false
	draft.load_source(snapshot)
	service_url = url
	heading.text = "%s · base revision %s" % [snapshot.story_id, int(snapshot.revision)]
	for choice in [character, variable, source_chapter, target_chapter]:
		choice.clear()
	for key in snapshot.bundle.package.registry.get("characters", {}):
		character.add_item(key)
		character.set_item_metadata(character.item_count - 1, key)
	for key in snapshot.bundle.package.registry.get("variables", {}):
		variable.add_item(key)
		variable.set_item_metadata(variable.item_count - 1, key)
	var summary: Array[String] = ["Story: " + snapshot.bundle.package.title,
		"Starts at: %s / node %s" % [snapshot.bundle.package.entry.document, snapshot.bundle.package.entry.node], ""]
	for chapter in snapshot.bundle.documents:
		for choice in [source_chapter, target_chapter]:
			choice.add_item(chapter)
			choice.set_item_metadata(choice.item_count - 1, chapter)
		summary.append("%s — %s\nSource: %s\n" % [chapter, snapshot.bundle.documents[chapter].title, snapshot.bundle.package.documents[chapter].path])
	overview.text = "\n".join(summary)
	_refresh_character()
	_refresh_variable()
	_refresh_jumps()
	_fill_nodes(target_node, _selected(target_chapter))
	_refresh_pending()
	status.text = "Editing the loaded snapshot. Refreshing the service will not rebase this batch."
	popup_centered()
	return true

func _usage(collection: String, key: String) -> String:
	var used: Array[String] = []
	for chapter in draft.source.bundle.package.documents:
		if key in draft.source.bundle.package.documents[chapter].get(collection, {}).values():
			used.append(chapter)
	return "Shared by: " + (", ".join(used) if not used.is_empty() else "no mapped chapters")

func _refresh_character() -> void:
	var key = _selected(character)
	character_name.editable = not key.is_empty()
	character_name.text = "" if key.is_empty() else str(draft.groups.get("character:" + key, {"operations": [{"value": draft.source.bundle.package.registry.characters[key].name}]}).operations[0].value)
	character_usage.text = "" if key.is_empty() else _usage("characters", key)

func _refresh_variable() -> void:
	var key = _selected(variable)
	default_bool.visible = false
	default_text.visible = false
	variable_type.text = ""
	variable_usage.text = ""
	if key.is_empty():
		return
	var item: Dictionary = draft.source.bundle.package.registry.variables[key]
	var value = draft.groups.get("variable:" + key, {"operations": [{"value": item.init}]}).operations[0].value
	variable_type.text = "Type: " + {"bool": "True / false", "num": "Whole number", "str": "Text"}.get(item.type, str(item.type))
	variable_usage.text = _usage("variables", key)
	if item.type == "bool":
		default_bool.visible = true
		default_bool.button_pressed = value
	else:
		default_text.visible = true
		default_text.text = str(int(value)) if item.type == "num" else str(value)

func _fill_nodes(choice: OptionButton, chapter: String, jumps_only: bool = false) -> void:
	choice.clear()
	if not draft.source.bundle.documents.has(chapter):
		return
	for id in draft.source.bundle.documents[chapter].resources.nodes:
		var node: Dictionary = draft.source.bundle.documents[chapter].resources.nodes[id]
		if not jumps_only or node.type == "jump":
			choice.add_item("%s · %s" % [node.name, id])
			choice.set_item_metadata(choice.item_count - 1, id)

func _refresh_jumps() -> void:
	_fill_nodes(source_jump, _selected(source_chapter), true)
	_fill_nodes(local_target, _selected(source_chapter))
	_refresh_link()

func _refresh_link() -> void:
	var chapter = _selected(source_chapter)
	var jump = _selected(source_jump)
	if jump.is_empty():
		current_link.text = "This chapter has no Jump nodes. Create one in Arrow and propose the chapter first."
		return
	var original = draft.link(chapter, jump)
	current_link.text = "Loaded destination: " + ("%s / node %s" % [original.to.document, original.to.node] if not original.is_empty() else "local node %s" % int(draft.source.bundle.documents[chapter].resources.nodes[jump].data.target))
	if not original.is_empty():
		_select(target_chapter, original.to.document)
		_fill_nodes(target_node, original.to.document)
		_select(target_node, original.to.node)
	else:
		_select(local_target, str(int(draft.source.bundle.documents[chapter].resources.nodes[jump].data.target)))

func stage_character() -> void:
	if not working:
		_staged(draft.stage_character(_selected(character), character_name.text))

func stage_variable() -> void:
	if working:
		return
	var key = _selected(variable)
	if key.is_empty():
		status.text = "Choose a shared variable first."
		return
	var type: String = draft.source.bundle.package.registry.variables[key].type
	var value: Variant = default_bool.button_pressed
	if type == "str":
		value = default_text.text
	elif type == "num":
		var number = default_text.text.strip_edges()
		if not number.is_valid_int() or not is_finite(number.to_float()) or abs(number.to_float()) > 9007199254740991.0:
			status.text = "Enter a whole number within ±9007199254740991."
			return
		value = number.to_int()
	_staged(draft.stage_variable(key, value))

func stage_link() -> void:
	if not working:
		_staged(draft.stage_link(_selected(source_chapter), _selected(source_jump), _selected(target_chapter), _selected(target_node)))

func stage_local_jump() -> void:
	if not working:
		_staged(draft.stage_local_jump(_selected(source_chapter), _selected(source_jump), _selected(local_target)))

func _staged(ok: bool) -> void:
	status.text = "Staged. Review the batch on the Staged changes tab, then propose it." if ok else draft.error
	_refresh_pending()

func _refresh_pending() -> void:
	pending.clear()
	for key in draft.groups:
		pending.add_item(draft.groups[key].label)
		pending.set_item_metadata(pending.item_count - 1, key)
	pending_count.text = "%s staged changes · %s operations" % [draft.groups.size(), draft.operations().size()]

func remove_selected() -> void:
	if working:
		return
	for index in pending.get_selected_items():
		draft.groups.erase(str(pending.get_item_metadata(index)))
	_refresh_pending()
	_refresh_character()
	_refresh_variable()
	status.text = "Selected staged changes removed."

func clear_staged() -> void:
	if working:
		return
	draft.groups.clear()
	_refresh_pending()
	_refresh_character()
	_refresh_variable()
	status.text = "Staged changes cleared. The shared story is unchanged."

func _set_working(value: bool) -> void:
	working = value
	for control in controls:
		if control is LineEdit:
			control.editable = not value
		elif control is BaseButton:
			control.disabled = value
		elif control is ItemList:
			control.mouse_filter = Control.MOUSE_FILTER_IGNORE if value else Control.MOUSE_FILTER_STOP

func propose() -> void:
	if working or service_window.working or draft.groups.is_empty():
		return
	if not service_window._configure() or service_window.client.base_url != service_url:
		status.text = "Reconnect to this batch's original service before proposing."
		return
	if message.text.strip_edges().is_empty():
		status.text = "Enter a review message before proposing the batch."
		return
	_set_working(true)
	status.text = "Validating the staged story changes…"
	var response = await service_window._send(HTTPClient.METHOD_POST, service_window._path(draft.source.story_id) + "/proposals", {
		"base_revision": int(draft.source.revision), "message": message.text, "operations": draft.operations()})
	_set_working(false)
	if response.ok:
		draft.groups.clear()
		_refresh_pending()
		service_window._show_review(response.value)
		service_window.status.text = "Story settings proposal saved (%s). Review its changes and diagnostics before applying." % response.value.status
		service_window.popup_centered()
		hide()
	else:
		status.text = "Proposal failed (HTTP %s). Staged edits and base revision are retained. See the service window for details." % response.status
