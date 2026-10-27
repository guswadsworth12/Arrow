extends SceneTree
const Story = preload("res://addons/arrow_dialogic/story.gd")
const PATH = "res://story/guild_story.json"
var checks: int = 0
var failures: Array[String] = []

func _initialize() -> void:
	var text = FileAccess.get_file_as_string(PATH)
	var package = JSON.parse_string(text)
	var linker = Story.new()
	var result = linker.compile(PATH, package, text)
	check(result.ok, "valid package compiles: " + str(result.errors))
	if not result.ok:
		printerr(result.errors)
		quit(1)
		return
	if result.ok:
		check(result.manifest.variable_defaults == {"request_accepted": false}, "one shared variable default")
		check(result.manifest.characters.size() == 1, "one shared character for different local UIDs")
		check(result.manifest.document_labels.followup["1"] == "arrow_followup__1", "readable stable chapter entry label")
		check("label arrow_briefing__10\njump arrow_followup__1" in result.timeline, "cross-document jump")
		check("if {request_accepted} == true:" in result.timeline, "shared variable condition in followup")
		check(result.timeline.count("if {request_accepted}") == 2, "both chapters use the same variable")
		check(not "set {request_accepted} = false" in result.timeline, "chapter entry never resets variables")
		check(result.manifest.chapter_entries.followup == "arrow_followup__1", "named chapter entry")
		check(result.manifest.document_dependencies == {"briefing": ["followup"], "followup": []}, "explicit chapter dependency graph")
		check(result.manifest.dependencies.size() == 3, "manifest and both documents tracked")
		check(Story.dependency_changes(result.manifest).is_empty(), "unchanged dependency snapshot")
		check(result.timeline == linker.compile(PATH, package, text).timeline, "deterministic repeated linking")
		var reordered = package.duplicate(true)
		reordered.documents = {"followup": package.documents.followup, "briefing": package.documents.briefing}
		check(linker.compile(PATH, reordered, text).timeline == result.timeline, "chapter order does not change labels or output")
		var renamed = package.duplicate(true)
		renamed.title = "A changed title"
		check(linker.compile(PATH, renamed, text).timeline == result.timeline, "display title does not change labels")
		var bound = linker.compile(PATH, package, text, {"rend": "ExistingRend"})
		check(bound.ok and '"ExistingRend": A new chapter begins.' in bound.timeline, "shared character binding in later chapter")
		var stale_manifest = result.manifest.duplicate(true)
		stale_manifest.dependencies[1].sha256 = "stale"
		check(Story.dependency_changes(stale_manifest) == ["res://story/briefing.arrow"], "changed chapter is identified")
	for field in ["format", "version", "id", "title", "registry", "documents", "entry", "links"]:
		for value in [null, [], true, 1.5]:
			var bad = package.duplicate(true)
			bad[field] = value
			reject(linker.compile(PATH, bad, text), "invalid %s: %s" % [field, value])
	for path in ["../briefing.arrow", "/tmp/briefing.arrow", "res://example.arrow", "a/../briefing.arrow", "./briefing.arrow", "missing.arrow", "a\\b.arrow", ""]:
		var bad = package.duplicate(true)
		bad.documents.briefing.path = path
		reject(linker.compile(PATH, bad, text), "unsafe or missing chapter path")
	var case_registry = package.duplicate(true)
	case_registry.registry.characters["Rend"] = {"name": "Another character"}
	var case_result = linker.compile(PATH, case_registry, text)
	check(case_result.ok, "case-distinct registry identities compile")
	if case_result.ok:
		var ids = case_result.manifest.registry_ids.characters
		var aliases = case_result.manifest.character_bindings
		check(aliases[ids.rend].to_lower() != aliases[ids.Rend].to_lower(), "case-distinct characters have portable distinct filenames")
	var bad = package.duplicate(true)
	bad.documents.followup.path = "briefing.arrow"
	reject(linker.compile(PATH, bad, text), "duplicate source path")
	bad = package.duplicate(true)
	bad.links.append(bad.links[0].duplicate(true))
	reject(linker.compile(PATH, bad, text), "ambiguous duplicate link source")
	for endpoint in ["from", "to"]:
		for field in ["document", "node"]:
			for value in [null, [], true, 1.5, "missing"]:
				bad = package.duplicate(true)
				bad.links[0][endpoint][field] = value
				reject(linker.compile(PATH, bad, text), "invalid link %s/%s" % [endpoint, field])
	bad = package.duplicate(true)
	bad.links[0].from.node = "2"
	reject(linker.compile(PATH, bad, text), "cannot override narration with a link")
	bad = package.duplicate(true)
	bad.links[0].to.node = "999"
	reject(linker.compile(PATH, bad, text), "missing destination node")
	bad = package.duplicate(true)
	bad.links = []
	reject(linker.compile(PATH, bad, text), "unlinked jump placeholder")
	bad = package.duplicate(true)
	bad.entry = {"document": "missing", "node": "1"}
	reject(linker.compile(PATH, bad, text), "missing story entry chapter")
	bad.entry = {"document": "followup", "node": "2"}
	reject(linker.compile(PATH, bad, text), "entry must be an entry node")
	for collection in ["characters", "variables"]:
		bad = package.duplicate(true)
		bad.documents.briefing[collection] = {}
		reject(linker.compile(PATH, bad, text), "unbound local resource")
		bad = package.duplicate(true)
		bad.registry[collection] = {}
		reject(linker.compile(PATH, bad, text), "unknown registry binding")
	bad = package.duplicate(true)
	bad.registry.variables.request_accepted.init = true
	reject(linker.compile(PATH, bad, text), "default mismatch with authored chapters")
	bad = package.duplicate(true)
	bad.registry.characters.rend.name = "SomeoneElse"
	reject(linker.compile(PATH, bad, text), "character identity mismatch")
	reject(linker.compile(PATH, package, text, {"unknown": "Rend"}), "unknown explicit character binding")
	bad = package.duplicate(true)
	bad.documents.briefing.path = "followup-link.arrow"
	check(DirAccess.open("res://story").create_link("followup.arrow", "res://story/followup-link.arrow") == OK, "symlink fixture created")
	reject(linker.compile(PATH, bad, text), "symlink dependencies rejected")
	DirAccess.remove_absolute("res://story/followup-link.arrow")
	# Registry defaults and resource definitions must never cause unchecked comparisons.
	for collection in ["characters", "variables"]:
		var key = "rend" if collection == "characters" else "request_accepted"
		for field in (["name", "type", "init"] if collection == "variables" else ["name"]):
			for value in [null, [], {}, true]:
				if field == "init" and value is bool:
					continue
				bad = package.duplicate(true)
				bad.registry[collection][key][field] = value
				reject(linker.compile(PATH, bad, text), "malformed registry %s/%s" % [collection, field])
	check(Story.dependency_changes({"dependencies": 4}) == ["Invalid dependency list"], "malformed dependency metadata")
	# The low-level compiler must reject unsafe or ambiguous custom labels.
	var raw = JSON.parse_string(FileAccess.get_file_as_string("res://example.arrow"))
	reject(Story.Compiler.new().compile(raw, {}, {"1": "arrow_2"}), "custom label collides with a default label")
	reject(Story.Compiler.new().compile(raw, {}, {"1": "label\ninjection"}), "custom label syntax")
	reject(Story.Compiler.new().compile(raw, {}, {"1": "label\n"}), "custom label with trailing newline")
	reject(Story.Compiler.new().compile(raw, {}, {"999": "unknown"}), "custom label points to missing node")
	# Validate missing-file and changed-content detection against actual file state.
	var chapter_path = "res://story/followup.arrow"
	var chapter_text = FileAccess.get_file_as_string(chapter_path)
	var file = FileAccess.open(chapter_path, FileAccess.WRITE)
	file.store_string("not JSON")
	file.close()
	reject(linker.compile(PATH, package, text), "invalid chapter JSON")
	check(Story.dependency_changes(result.manifest) == [chapter_path], "edited chapter detected from disk")
	file = FileAccess.open(chapter_path, FileAccess.WRITE)
	file.store_string(chapter_text)
	file.close()
	check(Story.dependency_changes(result.manifest).is_empty(), "restored dependency is current")
	check(DirAccess.rename_absolute(chapter_path, chapter_path + ".saved") == OK, "missing chapter fixture")
	check(Story.dependency_changes(result.manifest) == [chapter_path], "missing chapter identified")
	reject(linker.compile(PATH, package, text), "missing chapter stops linking")
	check(DirAccess.rename_absolute(chapter_path + ".saved", chapter_path) == OK, "missing chapter restored")
	var renamed_path = "res://story/renamed.arrow"
	file = FileAccess.open(renamed_path, FileAccess.WRITE)
	file.store_string(chapter_text)
	file.close()
	bad = package.duplicate(true)
	bad.documents.followup.path = "renamed.arrow"
	var relocated = linker.compile(PATH, bad, text)
	check(relocated.ok and relocated.timeline == result.timeline, "chapter file rename preserves timeline and labels")
	DirAccess.remove_absolute(renamed_path)
	# Explicit graph cycles are linkable; no termination guarantee is claimed.
	var cyclic = JSON.parse_string(chapter_text)
	cyclic.resources.nodes["6"] = {"type": "jump", "name": "return", "data": {"target": -1}}
	file = FileAccess.open(chapter_path, FileAccess.WRITE)
	file.store_string(JSON.stringify(cyclic))
	file.close()
	bad = package.duplicate(true)
	bad.links.append({"from": {"document": "followup", "node": "6"}, "to": {"document": "briefing", "node": "1"}})
	var cycle = linker.compile(PATH, bad, text)
	check(cycle.ok and "label arrow_followup__6\njump arrow_briefing__1" in cycle.timeline, "cross-chapter return link compiles")
	file = FileAccess.open(chapter_path, FileAccess.WRITE)
	file.store_string(chapter_text)
	file.close()
	print("Story package: %s checks, %s failures" % [checks, failures.size()])
	for failure in failures:
		printerr(failure)
	quit(0 if failures.is_empty() else 1)

func reject(result: Dictionary, message: String) -> void:
	check(not result.ok and result.timeline == "" and not result.errors.is_empty(), message)

func check(condition: bool, message: String) -> void:
	checks += 1
	if not condition:
		failures.append(message)
