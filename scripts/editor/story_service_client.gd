extends Node
## Native editor HTTP transport. Credentials remain in memory, never in source files.
const MAX_UPLOAD = 8 * 1024 * 1024
var base_url = ""
var bearer = ""
var busy = false
## Optional Cloudflare Access service token for the native client, from the launch environment.
var access_headers = PackedStringArray()
signal web_response(status: int, text: String)
var _web_callback

func _ready() -> void:
	if Html5Helpers.Utils.is_browser():
		JavaScriptBridge.eval(FileAccess.get_file_as_string("res://web/bridge.js"), true)
		_web_callback = JavaScriptBridge.create_callback(func(args): web_response.emit(int(args[0]), str(args[1])))
	else:
		var access_id = OS.get_environment("ARROW_STORY_ACCESS_CLIENT_ID")
		var access_secret = OS.get_environment("ARROW_STORY_ACCESS_CLIENT_SECRET")
		if not access_id.is_empty() and not access_secret.is_empty():
			access_headers = PackedStringArray(["CF-Access-Client-Id: " + access_id, "CF-Access-Client-Secret: " + access_secret])

## True when a browser PWA talks to the origin it was loaded from, where a Cloudflare Access sign-in applies.
static func same_origin(url: String) -> bool:
	return Html5Helpers.Utils.is_browser() and url.trim_suffix("/") == str(JavaScriptBridge.get_interface("location").origin)

static func valid_url(value: String) -> bool:
	var pattern = RegEx.new()
	pattern.compile("^(https?)://(\\[[0-9A-Fa-f:]+\\]|[A-Za-z0-9.-]+)(:[0-9]{1,5})?(/[A-Za-z0-9_./~-]*)?$")
	var found = pattern.search(value)
	if found == null or found.get_string(0) != value:
		return false
	return found.get_string(1) == "https" or found.get_string(2).to_lower() in ["localhost", "127.0.0.1", "[::1]"]

func configure(url: String, token: String) -> bool:
	url = url.trim_suffix("/")
	if not valid_url(url):
		return false
	# The same-origin PWA may rely on its Cloudflare Access sign-in cookie instead of a token.
	if token.is_empty() and same_origin(url):
		base_url = url
		bearer = ""
		return true
	if token.length() < 32:
		return false
	for character in token:
		if character.unicode_at(0) < 33 or character.unicode_at(0) > 126:
			return false
	base_url = url
	bearer = token
	return true

func request(method: HTTPClient.Method, path: String, body: Variant = null) -> Dictionary:
	if busy or base_url.is_empty() or not (path.begins_with("/v1/stories") or path == "/v1/session"):
		return {"ok": false, "status": 0, "value": {"error": {"message": "Configure the service and wait for the current request."}}}
	var payload = "" if body == null else JSON.stringify(body)
	if payload.to_utf8_buffer().size() > MAX_UPLOAD:
		return {"ok": false, "status": 413, "value": {"error": {"message": "Upload exceeds 8 MiB."}}}
	busy = true
	if Html5Helpers.Utils.is_browser():
		JavaScriptBridge.get_interface("ArrowStoryWeb").request(base_url + path, "GET" if method == HTTPClient.METHOD_GET else "POST", bearer, payload, _web_callback)
		var received = await web_response
		busy = false
		var value = JSON.parse_string(received[1])
		if not value is Dictionary:
			value = {"error": {"message": "Service returned invalid JSON."}}
			return {"ok": false, "status": received[0], "value": value}
		return {"ok": received[0] >= 200 and received[0] < 300, "status": received[0], "value": value}
	var http = HTTPRequest.new()
	http.max_redirects = 0
	http.timeout = 90
	http.body_size_limit = 64 * 1024 * 1024
	add_child(http)
	var started = http.request(base_url + path, PackedStringArray(["Authorization: Bearer " + bearer, "Content-Type: application/json"]) + access_headers, method, payload)
	if started != OK:
		http.queue_free()
		busy = false
		return {"ok": false, "status": 0, "value": {"error": {"message": "Could not start service request."}}}
	var result = await http.request_completed
	http.queue_free()
	busy = false
	var parsed = JSON.new()
	var response: PackedByteArray = result[3]
	if result[0] != HTTPRequest.RESULT_SUCCESS or parsed.parse(response.get_string_from_utf8()) != OK or not parsed.data is Dictionary:
		return {"ok": false, "status": result[1], "value": {"error": {"message": "Connection failed, timed out, redirected, or returned invalid JSON."}}}
	return {"ok": result[1] >= 200 and result[1] < 300, "status": result[1], "value": parsed.data}

static func read_json(path: String) -> Dictionary:
	var file = FileAccess.open(path, FileAccess.READ)
	if file == null or file.get_length() > MAX_UPLOAD:
		return {"ok": false, "error": "Source is missing, unreadable, or larger than 8 MiB."}
	var parsed = JSON.new()
	if parsed.parse(file.get_as_text()) != OK or not parsed.data is Dictionary:
		return {"ok": false, "error": "Source must be a JSON object."}
	return {"ok": true, "value": parsed.data}

static func read_bundle(manifest: String) -> Dictionary:
	if manifest.get_extension() != "json" or _is_link(manifest):
		return {"ok": false, "error": "Choose a .json story manifest without symlinks."}
	var loaded = read_json(manifest)
	if not loaded.ok:
		return loaded
	var package: Dictionary = loaded.value
	if not package.get("documents") is Dictionary or package.documents.is_empty() or package.documents.size() > 128:
		return {"ok": false, "error": "Manifest needs 1–128 documents."}
	var documents = {}
	var total = 0
	for chapter in package.documents:
		var spec = package.documents[chapter]
		if not spec is Dictionary or not spec.get("path") is String:
			return {"ok": false, "error": "Each chapter needs a relative source path."}
		var relative: String = spec.path
		if relative.is_empty() or relative.contains(":") or relative.contains("\\") or relative.get_extension().to_lower() not in ["arrow", "json"]:
			return {"ok": false, "error": "Chapter paths must be relative .arrow or .json files."}
		var path = manifest.get_base_dir()
		for part in relative.split("/"):
			if part in ["", ".", ".."]:
				return {"ok": false, "error": "Chapter paths cannot traverse directories."}
			path = path.path_join(part)
			if _is_link(path):
				return {"ok": false, "error": "Chapter symlinks are not supported."}
		var source = read_json(path)
		if not source.ok:
			return source
		documents[chapter] = source.value
		total += JSON.stringify(source.value).to_utf8_buffer().size()
		if total > MAX_UPLOAD:
			return {"ok": false, "error": "Package exceeds 8 MiB."}
	return {"ok": true, "value": {"manifest_path": manifest.get_file(), "package": package, "documents": documents}}

static func _is_link(path: String) -> bool:
	var directory = DirAccess.open(path.get_base_dir())
	return directory != null and directory.is_link(path.get_file())
