extends SceneTree
const Archive = preload("res://scripts/editor/story_archive.gd")
const Story = preload("res://addons/arrow_dialogic/story.gd")
var count = 0
func check(value: bool) -> void:
	count += 1
	assert(value, "Archive check %s failed" % count)

func _initialize() -> void:
	var text = FileAccess.get_file_as_string("res://story/guild_story.json")
	var package = JSON.parse_string(text)
	var bundle = {"manifest_path": "guild_story.json", "package": package, "documents": {}}
	var texts = {}
	for chapter in package.documents:
		texts[chapter] = FileAccess.get_file_as_string("res://story/" + package.documents[chapter].path)
		bundle.documents[chapter] = JSON.parse_string(texts[chapter])
	var compiler = Story.new()
	var file_result = compiler.compile("res://story/guild_story.json", package, text)
	var memory_result = compiler.compile_sources("res://story/guild_story.json", package, text, texts)
	check(file_result.ok and memory_result.ok)
	check(file_result.timeline == memory_result.timeline)
	check(file_result.manifest == memory_result.manifest)
	check(not compiler.compile_sources("res://story/guild_story.json", package, text, {}).ok)
	var bad_texts = texts.duplicate()
	bad_texts.briefing = []
	check(not compiler.compile_sources("res://story/guild_story.json", package, text, bad_texts).ok)
	bad_texts.briefing = "invalid JSON"
	check(not compiler.compile_sources("res://story/guild_story.json", package, text, bad_texts).ok)
	var packed = Archive.source_archive(bundle)
	check(packed.ok)
	var read = Archive.read_package(packed.bytes, "sources.zip")
	check(read.ok and read.value == bundle)
	check(Archive.read_package(JSON.stringify(bundle).to_utf8_buffer(), "bundle.json").ok)
	var compiled_zip = Archive.dialogic_archive(bundle)
	check(compiled_zip.ok)
	check(Archive.read_package(compiled_zip.bytes, "dialogic.zip").value == bundle)
	check(not Archive.read_package(PackedByteArray(), "empty.zip").ok)
	check(not Archive.read_package("invalid".to_utf8_buffer(), "bad.zip").ok)
	for path in ["../escape.arrow", "/absolute.arrow", "folder\\chapter.arrow", "res://chapter.arrow", "nested/../chapter.arrow", "a\n.arrow"]:
		var bad = bundle.duplicate(true)
		bad.package.documents.briefing.path = path
		check(not Archive.source_archive(bad).ok)
	var duplicate = bundle.duplicate(true)
	duplicate.package.documents.followup.path = "BRIEFING.ARROW"
	check(not Archive.source_archive(duplicate).ok)
	var unsupported = bundle.duplicate(true)
	unsupported.documents.briefing.resources.nodes["2"].type = "unsupported"
	check(not Archive.dialogic_archive(unsupported).ok)
	# Reject hostile central-directory metadata before decompression.
	var directory = -1
	for offset in range(packed.bytes.size() - 3):
		if packed.bytes.decode_u32(offset) == 0x02014b50:
			directory = offset
			break
	check(directory >= 0)
	for kind in ["expanded", "encrypted", "compression", "symlink", "traversal"]:
		var hostile: PackedByteArray = packed.bytes.duplicate()
		match kind:
			"expanded": hostile.encode_u32(directory + 24, Archive.MAX_EXPANDED + 1)
			"encrypted": hostile.encode_u16(directory + 8, 1)
			"compression": hostile.encode_u16(directory + 10, 12)
			"symlink": hostile.encode_u32(directory + 38, 0xa000 << 16)
			"traversal":
				hostile[directory + 46] = 46
				hostile[directory + 47] = 46
				hostile[directory + 48] = 47
		check(not Archive.read_package(hostile, "hostile.zip").ok)
	for value in [true, 1, [], {}]:
		var bad = bundle.duplicate(true)
		bad.package.format = value
		check(not Archive.source_archive(bad).ok)
	print("Story archives: %s checks, source ZIP round trip, in-memory/file compiler equivalence and invalid input verified" % count)
	quit(0)
