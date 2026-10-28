extends SceneTree
const Story = preload("res://addons/arrow_dialogic/story.gd")

func _initialize() -> void:
	var request = JSON.parse_string(FileAccess.get_file_as_string("res://request.json"))
	var path = "res://story/" + request.manifest_path
	var text = FileAccess.get_file_as_string(path)
	var result = Story.new().compile(path, JSON.parse_string(text), text)
	var output = {"ok": result.ok, "errors": result.errors}
	if result.ok:
		output["summary"] = {
			"node_count": result.manifest.node_labels.size(),
			"character_count": result.manifest.characters.size(),
			"variable_defaults": result.manifest.variable_defaults,
			"chapter_entries": result.manifest.chapter_entries,
			"document_labels": result.manifest.document_labels,
			"document_dependencies": result.manifest.document_dependencies,
		}
	var file = FileAccess.open("res://result.json", FileAccess.WRITE)
	file.store_string(JSON.stringify(output))
	file.close()
	quit(0)
