# Arrow
# Game Narrative Design Tool
# Mor. H. Golkar

# Shared helper classes for browser (i.e. HTML5 export)
class_name Html5Helpers

# Classes

# HTML5 Utilities 
class Utils:
	
	static func is_browser() -> bool:
		return OS.get_name() == "Web" || OS.has_feature("web")
	
	static func window() -> JavaScriptObject:
		return JavaScriptBridge.get_interface("window")
		
	static func document() -> JavaScriptObject:
		return JavaScriptBridge.get_interface("document")
	
	static func alert(msg: String) -> void:
		window().alert(msg)
		pass
	
	static func refresh_window() -> void:
		JavaScriptBridge.get_interface("location").reload()
		pass
	
	static func clear_browser_storage() -> void:
		JavaScriptBridge.get_interface("localStorage").clear()
		JavaScriptBridge.get_interface("sessionStorage").clear()
		JavaScriptBridge.get_interface("indexedDB").deleteDatabase("/userfs")
		refresh_window()
		pass
	
	static func close() -> void:
		window().close()
	
# HTML5 File Reader Helper
# This allows opening project files from the user's device file-system
class Reader:
	
	var _reader_callback_ref;
	var _file_reader;
	var _browser_callback_ref;
	var _browser_input;
	
	var _callback;
	
	var _browsed;
	
	func _init() -> void:
		if Utils.is_browser():
			self._reader_callback_ref = JavaScriptBridge.create_callback(self.read_file_data)
			self._file_reader = JavaScriptBridge.create_object("FileReader")
			self._file_reader.onload = self._reader_callback_ref
			self._browser_callback_ref = JavaScriptBridge.create_callback(self.browse_file)
			self._browser_input = Utils.document().createElement("input")
			self._browser_input.type = "file"
			self._browser_input.accept= Settings.PROJECT_FILE_EXTENSION + ",.json"
			self._browser_input.onchange = self._browser_callback_ref
		pass
		
	func read_file_data(args):
		var event = args[0]
		var content = event.target.result;
		self._callback.call_deferred(content, self._browsed.name)
		event.preventDefault()
		event.returnValue = ''
		# print_debug("browser read file: ", content)
		pass
	
	func browse_file(args):
		var event = args[0]
		var file = event.target.files[0]
		self._browsed = file
		self._file_reader.readAsText(file,'UTF-8');
		event.preventDefault()
		event.returnValue = ''
		pass
	
	func read_file_then(callback: Callable):
		if Utils.is_browser():
			self._callback = callback
			self._browser_input.click()
		else:
			printerr("HTML5 FS helpers read in context other than browser!")
		pass
	

# Binary package picker. Browser paths never become filesystem paths.
class BinaryReader:
	var _input
	var _reader
	var _change
	var _loaded
	var _cancel
	var _callback: Callable
	var _filename = ""
	func _init() -> void:
		if not Utils.is_browser():
			return
		_input = Utils.document().createElement("input")
		_input.type = "file"
		_input.accept = ".zip,.json"
		_reader = JavaScriptBridge.create_object("FileReader")
		_loaded = JavaScriptBridge.create_callback(func(args): _callback.call_deferred(JavaScriptBridge.js_buffer_to_packed_byte_array(args[0].target.result), _filename))
		_reader.onload = _loaded
		_cancel = JavaScriptBridge.create_callback(func(_args): _callback.call_deferred(PackedByteArray(), ""))
		_reader.onerror = _cancel
		_input.oncancel = _cancel
		_change = JavaScriptBridge.create_callback(func(args):
			var files = args[0].target.files
			if files.length > 0:
				_filename = str(files[0].name)
				if files[0].size > 32 * 1024 * 1024:
					_callback.call_deferred(PackedByteArray(), _filename)
				else:
					_reader.readAsArrayBuffer(files[0]))
		_input.onchange = _change
	func read_then(callback: Callable) -> void:
		_callback = callback
		_input.value = ""
		_input.click()
