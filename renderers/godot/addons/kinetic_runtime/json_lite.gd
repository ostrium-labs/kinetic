## Minimal JSON reader that keeps integers as int.
##
## Godot's JSON class parses every number as float, which would turn trace ticks and integer
## state fields into floats. Kinetic files are small, so a plain recursive-descent reader is
## fast enough. Usage: var r := JsonLite.new(); var v = r.parse(text); if r.error != "": ...
extends RefCounted

var error := ""
var _s := ""
var _i := 0
var _n := 0


func parse(text: String) -> Variant:
	_s = text
	_i = 0
	_n = text.length()
	error = ""
	_ws()
	var value: Variant = _value()
	_ws()
	if error == "" and _i != _n:
		_fail("trailing characters")
	return null if error != "" else value


func _fail(message: String) -> void:
	if error == "":
		error = "%s at offset %d" % [message, _i]


func _peek() -> String:
	return _s[_i] if _i < _n else ""


func _ws() -> void:
	while _i < _n:
		var c := _s.unicode_at(_i)
		if c == 32 or c == 9 or c == 10 or c == 13:
			_i += 1
		else:
			return


func _value() -> Variant:
	if error != "":
		return null
	match _peek():
		"":
			_fail("unexpected end of input")
			return null
		"{":
			return _object()
		"[":
			return _array()
		"\"":
			return _string()
		"t":
			return _literal("true", true)
		"f":
			return _literal("false", false)
		"n":
			return _literal("null", null)
	return _number()


func _literal(word: String, value: Variant) -> Variant:
	if _s.substr(_i, word.length()) != word:
		_fail("invalid literal")
		return null
	_i += word.length()
	return value


func _object() -> Variant:
	var out := {}
	_i += 1
	_ws()
	if _peek() == "}":
		_i += 1
		return out
	while error == "":
		_ws()
		if _peek() != "\"":
			_fail("expected a string key")
			return null
		var key := _string()
		_ws()
		if _peek() != ":":
			_fail("expected ':'")
			return null
		_i += 1
		_ws()
		out[key] = _value()
		_ws()
		var c := _peek()
		_i += 1
		if c == "}":
			return out
		if c != ",":
			_fail("expected ',' or '}'")
	return null


func _array() -> Variant:
	var out := []
	_i += 1
	_ws()
	if _peek() == "]":
		_i += 1
		return out
	while error == "":
		_ws()
		out.append(_value())
		_ws()
		var c := _peek()
		_i += 1
		if c == "]":
			return out
		if c != ",":
			_fail("expected ',' or ']'")
	return null


func _string() -> String:
	_i += 1
	var parts := PackedStringArray()
	var start := _i
	while _i < _n:
		var c := _s[_i]
		if c == "\"":
			parts.append(_s.substr(start, _i - start))
			_i += 1
			return "".join(parts)
		if c == "\\":
			parts.append(_s.substr(start, _i - start))
			if _i + 1 >= _n:
				break
			var e := _s[_i + 1]
			_i += 2
			match e:
				"\"", "\\", "/":
					parts.append(e)
				"b":
					parts.append(char(8))
				"f":
					parts.append(char(12))
				"n":
					parts.append("\n")
				"r":
					parts.append("\r")
				"t":
					parts.append("\t")
				"u":
					var code := _s.substr(_i, 4).hex_to_int()
					_i += 4
					if code >= 0xD800 and code <= 0xDBFF and _s.substr(_i, 2) == "\\u":
						var low := _s.substr(_i + 2, 4).hex_to_int()
						if low >= 0xDC00 and low <= 0xDFFF:
							code = 0x10000 + ((code - 0xD800) << 10) + (low - 0xDC00)
							_i += 6
					parts.append(char(code))
				_:
					_fail("invalid escape")
					return ""
			start = _i
			continue
		_i += 1
	_fail("unterminated string")
	return ""


func _number() -> Variant:
	var start := _i
	var is_float := false
	while _i < _n:
		var c := _s[_i]
		if "0123456789+-".contains(c):
			_i += 1
		elif c == "." or c == "e" or c == "E":
			is_float = true
			_i += 1
		else:
			break
	var token := _s.substr(start, _i - start)
	if is_float and token.is_valid_float():
		return token.to_float()
	if not is_float and token.is_valid_int():
		return token.to_int()
	_fail("invalid value")
	return null
