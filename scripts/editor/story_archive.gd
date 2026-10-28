extends RefCounted
## Portable source/compiled exports. ZIP contents are read in memory, never extracted.
const Story = preload("res://addons/arrow_dialogic/story.gd")
const MAX_SOURCE = 8 * 1024 * 1024
const MAX_ARCHIVE = 32 * 1024 * 1024
const MAX_EXPANDED = 64 * 1024 * 1024

static func failure(message: String) -> Dictionary:
	return {"ok": false, "error": message}

static func safe_path(path: Variant) -> bool:
	if not path is String or path.is_empty() or path.length() > 512 or path.contains(":") or path.contains("\\"):
		return false
	for character in path:
		if character.unicode_at(0) < 32:
			return false
	for part in path.split("/"):
		if part in ["", ".", ".."]:
			return false
	return true

static func sources(bundle: Variant) -> Dictionary:
	if not bundle is Dictionary or not bundle.get("package") is Dictionary or not bundle.get("documents") is Dictionary or not safe_path(bundle.get("manifest_path")):
		return failure("A source bundle needs manifest_path, package and documents.")
	if bundle.manifest_path.contains("/") or not bundle.manifest_path.ends_with(".json") or bundle.manifest_path.to_lower() == ".arrow-story-checkout.json":
		return failure("The manifest must be a .json filename at the root of the package.")
	var package: Dictionary = bundle.package
	if not package.get("format") is String or package.format != "arrow-story" or not package.get("documents") is Dictionary or package.documents.is_empty() or package.documents.size() > 128 or package.documents.size() != bundle.documents.size():
		return failure("Choose a story package with 1–128 matching chapter sources.")
	var files = {bundle.manifest_path: JSON.stringify(package).to_utf8_buffer()}
	var paths = {bundle.manifest_path.to_lower(): true, ".arrow-story-checkout.json": true}
	for chapter in package.documents:
		var spec = package.documents[chapter]
		if not spec is Dictionary or not safe_path(spec.get("path")) or spec.path.get_extension().to_lower() not in ["arrow", "json"] or not bundle.documents.get(chapter) is Dictionary:
			return failure("Each chapter needs a relative .arrow/.json path and source object.")
		var path: String = spec.path
		if paths.has(path.to_lower()):
			return failure("Source paths must be unique, including case.")
		paths[path.to_lower()] = true
		files[path] = JSON.stringify(bundle.documents[chapter]).to_utf8_buffer()
	for path in paths:
		var parent = path.get_base_dir()
		while not parent.is_empty():
			if paths.has(parent):
				return failure("A source file cannot also be a directory.")
			parent = parent.get_base_dir()
	if JSON.stringify(bundle).to_utf8_buffer().size() > MAX_SOURCE:
		return failure("A complete source bundle cannot exceed 8 MiB.")
	return {"ok": true, "files": files}

static func pack(files: Dictionary) -> Dictionary:
	var path = "user://arrow-export-%s.zip" % Time.get_ticks_usec()
	var writer = ZIPPacker.new()
	if writer.open(path) != OK:
		return failure("Could not prepare the export archive.")
	var status = OK
	for filename in files:
		if not safe_path(filename) or writer.start_file(filename) != OK:
			status = FAILED
			break
		if writer.write_file(files[filename]) != OK or writer.close_file() != OK:
			status = FAILED
			break
	if writer.close() != OK:
		status = FAILED
	var bytes = FileAccess.get_file_as_bytes(path) if status == OK else PackedByteArray()
	DirAccess.remove_absolute(path)
	return {"ok": true, "bytes": bytes} if not bytes.is_empty() else failure("Could not complete the export archive.")

static func source_archive(bundle: Dictionary) -> Dictionary:
	var result = sources(bundle)
	return pack(result.files) if result.ok else result

static func dialogic_archive(bundle: Dictionary) -> Dictionary:
	var input = sources(bundle)
	if not input.ok:
		return input
	var texts: Dictionary = {}
	for chapter in bundle.package.documents:
		texts[chapter] = input.files[bundle.package.documents[chapter].path].get_string_from_utf8()
	var id = bundle.package.get("id")
	if not id is String or not Story.new()._name(id):
		return failure("Story id must be an identifier.")
	var virtual_path = "res://sources/" + id + "/" + bundle.manifest_path
	var compiled = Story.new().compile_sources(virtual_path, bundle.package, input.files[bundle.manifest_path].get_string_from_utf8(), texts)
	if not compiled.ok:
		return failure("Export stopped.\n" + "\n".join(compiled.errors))
	var folder = "dialogic_export/" + id
	var files: Dictionary = {}
	for filename in input.files:
		files["sources/" + id + "/" + filename] = input.files[filename]
	files[folder + "/story.dtl"] = compiled.timeline.to_utf8_buffer()
	files[folder + "/manifest.json"] = JSON.stringify(compiled.manifest, "  ").to_utf8_buffer()
	var characters: Dictionary = {}
	for key in compiled.manifest.characters:
		var alias: String = compiled.manifest.character_bindings[key]
		var path = folder + "/characters/" + alias + ".dch"
		files[path] = var_to_str({"@path": "res://addons/dialogic/Resources/character.gd", "@subpath": NodePath(""), &"display_name": compiled.manifest.characters[key].name}).to_utf8_buffer()
		characters[alias] = "res://" + path
	var settings = ConfigFile.new()
	settings.set_value("dialogic", "variables", compiled.manifest.variable_defaults)
	settings.set_value("dialogic", "directories/dch_directory", characters)
	settings.set_value("dialogic", "directories/dtl_directory", {"arrow_export_" + id: "res://" + folder + "/story.dtl"})
	files["project-snippet.godot"] = settings.encode_to_text().to_utf8_buffer()
	files["README.txt"] = ("Arrow to Dialogic 2.0-alpha-20 export\n\nCopy dialogic_export/ and sources/ into your Godot project with pinned Dialogic installed and enabled.\nMerge project-snippet.godot's Dialogic settings with your project settings; preserve existing characters and authored defaults. Do not replace your entire project.godot.\nStart with Dialogic.start(\"res://" + folder + "/story.dtl\"). Chapter entry labels are in manifest.json.\nSources are in sources/. For repeat imports with preservation and dependency checks, use the Arrow Godot addon on those sources instead.\nGenerated characters contain display names only. Game state, save/load and quests remain owned by Godot.\n").to_utf8_buffer()
	return pack(files)

static func read_package(bytes: PackedByteArray, filename: String) -> Dictionary:
	if bytes.is_empty() or bytes.size() > MAX_ARCHIVE:
		return failure("Choose a package ZIP or bundle JSON of at most 32 MiB.")
	if filename.to_lower().ends_with(".json"):
		var bundle = JSON.parse_string(bytes.get_string_from_utf8())
		var checked = sources(bundle)
		return {"ok": true, "value": bundle} if checked.ok else checked
	var guard = _zip_inventory(bytes)
	if not guard.ok:
		return guard
	var path = "user://arrow-package-%s.zip" % Time.get_ticks_usec()
	var file = FileAccess.open(path, FileAccess.WRITE)
	if file == null:
		return failure("Could not read the uploaded package.")
	file.store_buffer(bytes)
	file.close()
	var reader = ZIPReader.new()
	if reader.open(path) != OK:
		DirAccess.remove_absolute(path)
		return failure("Invalid ZIP archive.")
	var manifests: Array = []
	var data: Dictionary = {}
	var valid = true
	for name in guard.files:
		var contents = reader.read_file(name)
		if contents.size() != guard.files[name]:
			valid = false
			break
		data[name] = contents
		if name.ends_with(".json"):
			var value = JSON.parse_string(contents.get_string_from_utf8())
			if value is Dictionary and value.get("format") is String and value.format == "arrow-story":
				manifests.append({"manifest_path": name.get_file(), "package": value, "documents": {}, "base_dir": name.get_base_dir()})
	reader.close()
	DirAccess.remove_absolute(path)
	if not valid or manifests.size() != 1:
		return failure("ZIP must contain one story manifest and all referenced chapters.")
	var bundle: Dictionary = manifests[0]
	var base_dir: String = bundle.base_dir
	bundle.erase("base_dir")
	if not bundle.package.get("documents") is Dictionary:
		return failure("Manifest needs chapter definitions.")
	for chapter in bundle.package.documents:
		var spec = bundle.package.documents[chapter]
		if not spec is Dictionary or not safe_path(spec.get("path")) or not data.has(base_dir.path_join(spec.path) if not base_dir.is_empty() else spec.path):
			return failure("A referenced chapter is missing or has an unsafe path.")
		bundle.documents[chapter] = JSON.parse_string(data[base_dir.path_join(spec.path) if not base_dir.is_empty() else spec.path].get_string_from_utf8())
	var checked = sources(bundle)
	return {"ok": true, "value": bundle} if checked.ok else checked

static func _zip_inventory(bytes: PackedByteArray) -> Dictionary:
	# Bound allocation before ZIPReader decompresses anything. ZIP64, encryption,
	# duplicate/traversing paths, symlinks and unsupported compression are rejected.
	var end = -1
	for offset in range(bytes.size() - 22, maxi(-1, bytes.size() - 65558), -1):
		if bytes.decode_u32(offset) == 0x06054b50 and offset + 22 + bytes.decode_u16(offset + 20) == bytes.size():
			end = offset
			break
	if end < 0 or bytes.decode_u16(end + 4) != 0 or bytes.decode_u16(end + 6) != 0:
		return failure("Invalid or unsupported ZIP archive.")
	var count = bytes.decode_u16(end + 10)
	var start = bytes.decode_u32(end + 16)
	var limit = start + bytes.decode_u32(end + 12)
	if count < 1 or count > 512 or bytes.decode_u16(end + 8) != count or limit > end:
		return failure("ZIP inventory exceeds the package limits.")
	var offset = start
	var total = 0
	var files: Dictionary = {}
	var seen: Dictionary = {}
	for index in count:
		if offset + 46 > limit or bytes.decode_u32(offset) != 0x02014b50:
			return failure("Invalid ZIP inventory.")
		var length = bytes.decode_u16(offset + 28)
		var next = offset + 46 + length + bytes.decode_u16(offset + 30) + bytes.decode_u16(offset + 32)
		var size = bytes.decode_u32(offset + 24)
		if next > limit or size > MAX_EXPANDED or bytes.decode_u16(offset + 8) & 1 or bytes.decode_u16(offset + 10) not in [0, 8] or (bytes.decode_u32(offset + 38) >> 16) & 0xf000 == 0xa000:
			return failure("Unsupported ZIP entry.")
		var name = bytes.slice(offset + 46, offset + 46 + length).get_string_from_utf8()
		if not safe_path(name.trim_suffix("/")) or seen.has(name.trim_suffix("/").to_lower()):
			return failure("ZIP paths must be safe and unique, including case.")
		seen[name.trim_suffix("/").to_lower()] = true
		total += size
		if total > MAX_EXPANDED:
			return failure("Expanded ZIP exceeds 64 MiB.")
		if not name.ends_with("/"):
			files[name] = size
		offset = next
	if offset != limit:
		return failure("Invalid ZIP inventory length.")
	return {"ok": true, "files": files}
