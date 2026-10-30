extends Control
## Status-line marker. Its *shape* carries the state as well as its colour, so the status
## line never relies on hue alone: "dash" = two short bars (fault, matches the dashed link),
## "ring" = an outline circle (timer fired), "dot" = a solid disc (delivered).

var kind := "dot"
var tint := Color.WHITE
var stroke := 2.0


func set_mark(k: String, c: Color, w: float) -> void:
	if k == kind and c == tint and w == stroke:
		return
	kind = k
	tint = c
	stroke = w
	queue_redraw()


func _draw() -> void:
	var s := size
	var cc := s * 0.5
	var r := minf(s.x, s.y) * 0.5
	match kind:
		"dash":
			var bw := s.x * 0.36
			var bh := maxf(stroke * 2.0, s.y * 0.26)
			draw_rect(Rect2(Vector2(0, cc.y - bh * 0.5), Vector2(bw, bh)), tint)
			draw_rect(Rect2(Vector2(s.x - bw, cc.y - bh * 0.5), Vector2(bw, bh)), tint)
		"ring":
			draw_arc(cc, r - stroke * 0.5, 0.0, TAU, 48, tint, stroke, true)
		_:
			draw_circle(cc, r * 0.62, tint, true, -1.0, true)
