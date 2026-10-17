# -*- coding: utf-8 -*-
"""
QBasic IDE for Android (Kivy front end).  Uses the same interpreter / flowchart engine as the desktop app.
Tabs:  CODE  |  OUTPUT  |  CHART (flowchart, DFD 0, DFD 1 - lights up live while the program runs)
"""
import os, sys, time, math, threading

from kivy import kivy_data_dir
from kivy.app import App
from kivy.clock import Clock
from kivy.core.text import Label as CoreLabel
from kivy.graphics import Color, Rectangle, RoundedRectangle, Line, Mesh, Ellipse, Triangle
from kivy.metrics import dp, sp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.label import Label
from kivy.uix.popup import Popup
from kivy.uix.scatter import Scatter
from kivy.uix.scrollview import ScrollView
from kivy.uix.spinner import Spinner
from kivy.uix.tabbedpanel import TabbedPanel, TabbedPanelItem
from kivy.uix.textinput import TextInput
from kivy.uix.togglebutton import ToggleButton
from kivy.uix.widget import Widget
from kivy.utils import get_color_from_hex, escape_markup

import qb_engine as qb

_mono = os.path.join(kivy_data_dir, 'fonts', 'RobotoMono-Regular.ttf')
MONO = _mono if os.path.exists(_mono) else 'Roboto'
KIND = {'start': 'START', 'end': 'END', 'stop': 'END', 'proc': 'PROCESS', 'in': 'INPUT', 'out': 'OUTPUT',
        'if': 'DECISION', 'loop': 'LOOP', 'post': 'LOOP', 'select': 'DECISION', 'call': 'CALL',
        'goto': 'JUMP', 'exit': 'EXIT', 'label': 'LABEL', 'case': 'CASE'}
DELAYS = {'Trace off': None, '0 ms': 0, '150 ms': 150, '300 ms': 300, '600 ms': 600, '1000 ms': 1000}
INK = (0.13, 0.13, 0.13, 1)
_tex = {}


def paint(widget, rgba):
    with widget.canvas.before:
        Color(*rgba)
        rect = Rectangle(pos=widget.pos, size=widget.size)
    widget.bind(pos=lambda i, v: setattr(rect, 'pos', v), size=lambda i, v: setattr(rect, 'size', v))


def texture(text, size, color, bold=False):
    key = (text, size, color, bold)
    t = _tex.get(key)
    if t is None:
        cl = CoreLabel(text=text, font_size=size, color=color, bold=bold, halign='center')
        cl.refresh()
        t = _tex[key] = cl.texture
    return t


class QBApp(App):
    title = 'QBasic IDE'

    # ------------------------------------------------------------------ UI
    def build(self):
        sys.setrecursionlimit(20000)
        self.io = qb.GuiIO()
        self.io.finished = True
        self.running = False
        self.live, self.delay = True, 300
        self.prims, self.tag_info = [], {}
        self.visited, self.cur = set(), []
        self.mode = 'flow'
        self.save_path = os.path.join(self.user_data_dir, 'last.bas')

        root = BoxLayout(orientation='vertical')
        bar = BoxLayout(size_hint_y=None, height=dp(46), spacing=dp(2), padding=dp(2))
        self._btn(bar, 'RUN', self.run_program, (0.1, 0.6, 0.2, 1))
        self._btn(bar, 'STOP', lambda *a: self.io.stop(), (0.75, 0.15, 0.15, 1))
        self.pause_btn = self._btn(bar, 'Pause', self.toggle_pause)
        self._btn(bar, 'Step', self.step_once)
        self.delay_sp = Spinner(text='300 ms', values=list(DELAYS), size_hint_x=1.4)
        self.delay_sp.bind(text=self.on_delay)
        bar.add_widget(self.delay_sp)
        root.add_widget(bar)

        self.tabs = TabbedPanel(do_default_tab=False, tab_pos='bottom_mid', tab_height=dp(42), tab_width=dp(110))
        self.t_code = TabbedPanelItem(text='CODE')
        self.t_out = TabbedPanelItem(text='OUTPUT')
        self.t_chart = TabbedPanelItem(text='CHART')
        self.t_code.add_widget(self._code_tab())
        self.t_out.add_widget(self._out_tab())
        self.t_chart.add_widget(self._chart_tab())
        self.t_chart.bind(on_release=lambda *a: self.refresh_chart())
        for t in (self.t_code, self.t_out, self.t_chart):
            self.tabs.add_widget(t)
        self.tabs.default_tab = self.t_code
        self.tabs.switch_to(self.t_code)
        root.add_widget(self.tabs)
        Clock.schedule_interval(self.tick, 0.05)
        return root

    def _btn(self, parent, text, cb, color=None):
        b = Button(text=text, on_release=cb, font_size=sp(13))
        if color:
            b.background_normal = ''
            b.background_color = color
        parent.add_widget(b)
        return b

    def _code_tab(self):
        box = BoxLayout(orientation='vertical')
        top = BoxLayout(size_hint_y=None, height=dp(42), spacing=dp(2))
        ex = Spinner(text='Examples', values=list(qb.EXAMPLES))
        ex.bind(text=self.pick_example)
        top.add_widget(ex)
        self._btn(top, 'Clear', lambda *a: setattr(self.editor, 'text', ''))
        self._btn(top, 'Save', self.save_program)
        self._btn(top, 'Load', self.load_program)
        box.add_widget(top)
        saved = ''
        if os.path.exists(self.save_path):
            try:
                saved = open(self.save_path, encoding='utf-8').read()
            except Exception:
                pass
        self.editor = TextInput(text=saved, font_name=MONO, font_size=sp(15), multiline=True,
                                background_color=(0, 0, 0.67, 1), foreground_color=(1, 1, 1, 1),
                                cursor_color=(1, 1, 1, 1), hint_text='Type your QBasic program here...',
                                hint_text_color=(0.7, 0.7, 1, 1), write_tab=False)
        box.add_widget(self.editor)
        keys = BoxLayout(size_hint_y=None, height=dp(40), spacing=dp(1))
        for k in ('"', '$', '(', ')', '=', '<>', ':', ';', ',', 'TAB'):
            keys.add_widget(Button(text=k, font_size=sp(15), on_release=lambda b, k=k: self.insert(k)))
        box.add_widget(keys)
        return box

    def _out_tab(self):
        box = BoxLayout(orientation='vertical')
        self.out_sv = ScrollView(do_scroll_x=True, do_scroll_y=True, bar_width=dp(5))
        paint(self.out_sv, (0, 0, 0, 1))
        self.out_lbl = Label(markup=True, font_name=MONO, font_size=sp(12), size_hint=(None, None),
                             halign='left', valign='top', color=(0.67, 0.67, 0.67, 1))
        self.out_sv.add_widget(self.out_lbl)
        box.add_widget(self.out_sv)
        row = BoxLayout(size_hint_y=None, height=dp(46), spacing=dp(2))
        self.inp = TextInput(multiline=False, font_name=MONO, font_size=sp(15),
                             hint_text='answer INPUT here, then Enter', write_tab=False)
        self.inp.bind(on_text_validate=self.on_submit)
        row.add_widget(self.inp)
        self._btn(row, 'Enter', self.on_submit)
        box.add_widget(row)
        cl = CoreLabel(text='M', font_name=MONO, font_size=sp(12))
        cl.refresh()
        self.cw, self.ch = cl.texture.size
        return box

    def _chart_tab(self):
        box = BoxLayout(orientation='vertical')
        paint(box, (1, 1, 1, 1))
        row = BoxLayout(size_hint_y=None, height=dp(40), spacing=dp(2))
        for text, mode in (('Flowchart', 'flow'), ('DFD 0', 'dfd0'), ('DFD 1', 'dfd1')):
            row.add_widget(ToggleButton(text=text, group='cm', state='down' if mode == 'flow' else 'normal',
                                        allow_no_selection=False, font_size=sp(12),
                                        on_press=lambda b, m=mode: self.set_mode(m)))
        self._btn(row, 'Fit', lambda *a: self.fit())
        self._btn(row, 'Export', self.export_chart)
        box.add_widget(row)
        self.caption = Label(text='', size_hint_y=None, height=dp(28), color=(0, 0, 0, 1), font_size=sp(13),
                             markup=True)
        box.add_widget(self.caption)
        self.holder = FloatLayout()
        self.scatter = Scatter(do_rotation=False, scale_min=0.1, scale_max=6.0, size_hint=(None, None),
                               size=(10, 10), auto_bring_to_front=False)
        self.chart = Widget(size_hint=(None, None), size=(10, 10))
        self.scatter.add_widget(self.chart)
        self.holder.add_widget(self.scatter)
        box.add_widget(self.holder)
        return box

    # ------------------------------------------------------------------ helpers
    def popup(self, title, msg):
        Popup(title=title, content=Label(text=msg, halign='center'), size_hint=(0.85, 0.4)).open()

    def insert(self, k):
        self.editor.insert_text('    ' if k == 'TAB' else k)
        Clock.schedule_once(lambda dt: setattr(self.editor, 'focus', True), 0.05)

    def pick_example(self, spinner, text):
        if text in qb.EXAMPLES:
            self.editor.text = qb.EXAMPLES[text]
            spinner.text = 'Examples'

    def save_program(self, *a):
        try:
            with open(self.save_path, 'w', encoding='utf-8') as fh:
                fh.write(self.editor.text)
            self.popup('Saved', 'Program saved.\n(Auto-restored next time you open the app)')
        except Exception as ex:
            self.popup('Save failed', str(ex))

    def load_program(self, *a):
        if os.path.exists(self.save_path):
            self.editor.text = open(self.save_path, encoding='utf-8').read()
        else:
            self.popup('Load', 'Nothing saved yet.')

    def on_pause(self):
        try:
            open(self.save_path, 'w', encoding='utf-8').write(self.editor.text)
        except Exception:
            pass
        return True

    def on_stop(self):
        self.on_pause()
        self.io.stop()

    def on_delay(self, sp_, text):
        d = DELAYS.get(text, 300)
        self.live, self.delay = d is not None, (d or 0)

    # ------------------------------------------------------------------ running
    def run_program(self, *a):
        if self.running:
            self.io.stop()
        io = qb.GuiIO()
        it = qb.Interpreter(io)
        try:
            it.compile(self.editor.text)
        except qb.QBError as e:
            self.popup('Error', '%s\n\nLine %s' % (e.msg, e.line))
            return
        self.io = io
        io.live, io.delay = self.live, self.delay
        self.pause_btn.text = 'Pause'
        self.visited, self.cur = set(), []
        self.mode = 'flow'
        self.refresh_chart(force=True)
        self.running = True
        self.tabs.switch_to(self.t_out)
        try:
            threading.stack_size(64 * 1024 * 1024)
        except Exception:
            try:
                threading.stack_size(16 * 1024 * 1024)
            except Exception:
                pass
        threading.Thread(target=self._runner, args=(it, io), daemon=True).start()
        Clock.schedule_once(lambda dt: setattr(self.inp, 'focus', True), 0.3)

    def _runner(self, it, io):
        try:
            it.run()
        except Exception as ex:
            io.error('Internal error: %r' % ex, 0)
        finally:
            io.screen.write('\nProgram finished')
            io.events.append(('END',))
            io.finished = True

    def toggle_pause(self, *a):
        self.io.paused = not self.io.paused
        self.pause_btn.text = 'Resume' if self.io.paused else 'Pause'

    def step_once(self, *a):
        self.io.paused = True
        self.io.step_req = True
        self.pause_btn.text = 'Resume'

    def on_submit(self, *a):
        txt = self.inp.text
        self.inp.text = ''
        for ch in txt:
            self.io.key_press(ch)
        self.io.key_press('\r')
        Clock.schedule_once(lambda dt: setattr(self.inp, 'focus', True), 0.1)

    # ------------------------------------------------------------------ periodic update
    def tick(self, dt):
        io = self.io
        io.live, io.delay = self.live, self.delay
        if io.screen.dirty:
            self.render_output()
        if io.events:
            evs = []
            while io.events:
                evs.append(io.events.popleft())
            for ev in evs[-200:]:
                self.apply_trace(ev)
            self.draw_chart()
        if self.running and io.finished:
            self.running = False

    def render_output(self):
        s = self.io.screen
        with s.lock:
            rows = [list(r) for r in s.grid]
            cr = s.r
            s.dirty = False
        last = max([i for i, r in enumerate(rows) if any(c[0] != ' ' for c in r)] + [cr, 0])
        lines = []
        for row in rows[:last + 1]:
            while row and row[-1][0] == ' ':
                row.pop()
            j, parts = 0, []
            while j < len(row):
                fg, k = row[j][1], j
                while k < len(row) and row[k][1] == fg:
                    k += 1
                parts.append('[color=%s]%s[/color]' % (qb.PAL[fg][1:], escape_markup(''.join(c[0] for c in row[j:k]))))
                j = k
            lines.append(''.join(parts))
        self.out_lbl.text = '\n'.join(lines)
        size = (max(self.out_sv.width, 80 * self.cw + dp(8)), max(self.out_sv.height, (len(lines) + 1) * self.ch))
        self.out_lbl.size = size
        self.out_lbl.text_size = size
        self.out_sv.scroll_y = 0

    # ------------------------------------------------------------------ chart
    def set_mode(self, m):
        self.mode = m
        self.refresh_chart(force=True)

    def refresh_chart(self, force=False):
        if self.running and self.prims and not force:
            return
        src = self.editor.text
        if not src.strip():
            self.prims, self.tag_info = [], {}
            self.draw_chart()
            self.caption.text = 'Type a program first'
            return
        it = qb.Interpreter(qb.HeadlessIO())
        try:
            it.compile(src)
            if self.mode == 'flow':
                prims = qb.build_flowchart(it, lambda s: 7.4 * len(s))
            else:
                prims = qb.build_dfd(it, 0 if self.mode == 'dfd0' else 1)
        except qb.QBError as e:
            self.caption.text = '[color=aa0000]%s - line %s[/color]' % (escape_markup(e.msg), e.line)
            return
        self.caption.text = ''
        self.prims = prims
        self.tag_info = {}
        for p in prims:
            if p[0] == 'shape':
                ln = p[7]
                tag = ln if isinstance(ln, str) else ('L%d' % ln if ln else None)
                if tag:
                    self.tag_info.setdefault(tag, (p[1], ' '.join(p[6])))
        self.draw_chart()
        Clock.schedule_once(lambda dt: self.fit(), 0.05)

    def apply_trace(self, tags):
        for t in self.cur:
            self.visited.add(t)
        self.cur = [t for t in tags if t in self.tag_info]
        if self.cur:
            kind, text = self.tag_info[self.cur[0]]
            self.caption.text = '[b]%s[/b]  %s' % (KIND.get(kind, kind.upper()), escape_markup(text))

    def fit(self, *a):
        if not self.prims:
            return
        W, H = self.scatter.size
        hw, hh = self.holder.size
        s = max(0.1, min(hw / max(W, 1), hh / max(H, 1), 2.0))
        self.scatter.scale = s
        self.scatter.pos = (0, hh - H * s)

    def draw_chart(self):
        w, prims = self.chart, self.prims
        w.canvas.clear()
        if not prims:
            return
        x0, y0, x1, y1 = qb.prim_bounds(prims)
        W, H = x1 - x0, y1 - y0
        if abs(self.chart.width - W) > 1 or abs(self.chart.height - H) > 1:
            self.chart.size = (W, H)
            self.scatter.size = (W, H)

        def X(v): return v - x0
        def Y(v): return H - (v - y0)

        def txt(s, x, y, size=13, color=(0.07, 0.07, 0.07, 1), bold=False):
            t = texture(s, size, color, bold)
            Color(1, 1, 1, 1)
            Rectangle(texture=t, pos=(x - t.width / 2, y - t.height / 2), size=t.size)

        def poly(pts, fill, outline, ow):
            verts = []
            for x, y in pts:
                verts += [x, y, 0, 0]
            Color(*fill)
            Mesh(vertices=verts, indices=list(range(len(pts))), mode='triangle_fan')
            Color(*outline)
            Line(points=[c for pt in pts for c in pt], width=ow, close=True)

        def arrow(a, b):
            dx, dy = b[0] - a[0], b[1] - a[1]
            n = math.hypot(dx, dy) or 1
            ux, uy, s = dx / n, dy / n, 11
            bx, by = b[0] - ux * s, b[1] - uy * s
            Color(*INK)
            Triangle(points=[b[0], b[1], bx - uy * 4.5, by + ux * 4.5, bx + uy * 4.5, by - ux * 4.5])

        def lab_col(s):
            return (0.69, 0, 0.13, 1) if s in ('No', 'Exit') else (0, 0.39, 0, 1)

        with w.canvas:
            Color(1, 1, 1, 1)
            Rectangle(pos=(0, 0), size=(W, H))
            for p in prims:
                k = p[0]
                if k == 'line':
                    pts = [(X(a), Y(b)) for a, b in p[1]]
                    Color(*INK)
                    Line(points=[c for pt in pts for c in pt], width=1.4)
                    if p[2]:
                        arrow(pts[-2], pts[-1])
                    if p[3]:
                        if len(p) > 4:
                            mx, my = (pts[0][0] + pts[-1][0]) / 2, (pts[0][1] + pts[-1][1]) / 2
                            t = texture(p[3], 12, (0.13, 0.13, 0.13, 1), True)
                            Color(1, 1, 1, 1)
                            Rectangle(pos=(mx - t.width / 2 - 2, my - t.height / 2), size=(t.width + 4, t.height))
                            txt(p[3], mx, my, 12, (0.13, 0.13, 0.13, 1), True)
                        else:
                            (ax, ay), (bx, by) = p[1][0], p[1][1]
                            n = math.hypot(bx - ax, by - ay) or 1
                            txt(p[3], X(ax + (bx - ax) / n * 20), Y(ay + (by - ay) / n * 20) + 10, 12,
                                lab_col(p[3]), True)
                elif k == 'label':
                    txt(p[3], X(p[1]), Y(p[2]), 12, lab_col(p[3]), True)
                elif k == 'title':
                    txt(p[3], X(p[1]), Y(p[2]), 16, (0, 0, 0.67, 1), True)
                elif k == 'shape':
                    self._shape(p, X, Y, poly, txt)
                elif k == 'dshape':
                    _, kind, cx, cy, sw, sh, lines = p
                    l, r, top, bot = X(cx - sw / 2), X(cx + sw / 2), Y(cy - sh / 2), Y(cy + sh / 2)
                    if kind == 'process':
                        Color(*get_color_from_hex('#D6E6FF'))
                        Ellipse(pos=(l, bot), size=(r - l, top - bot))
                        Color(*INK)
                        Line(ellipse=(l, bot, r - l, top - bot), width=1.6)
                    elif kind == 'entity':
                        Color(*get_color_from_hex('#FFF1B8'))
                        Rectangle(pos=(l, bot), size=(r - l, top - bot))
                        Color(*INK)
                        Line(rectangle=(l, bot, r - l, top - bot), width=1.6)
                    else:
                        Color(*get_color_from_hex('#E3F4E3'))
                        Rectangle(pos=(l, bot), size=(r - l, top - bot))
                        Color(*INK)
                        Line(points=[r, top, l, top, l, bot, r, bot], width=1.6)
                    txt('\n'.join(lines), X(cx), Y(cy))

    def _shape(self, p, X, Y, poly, txt):
        _, t, cx, cy, sw, sh, lines, ln = p
        tag = ln if isinstance(ln, str) else ('L%d' % ln if ln else None)
        fill, outline, ow = get_color_from_hex(qb.FLOW_FILL.get(t, '#FFFFFF')), INK, 1.5
        if tag in self.cur:
            fill, outline, ow = get_color_from_hex('#FFEB3B'), (0.84, 0, 0, 1), 4
        elif tag in self.visited:
            outline, ow = (0.18, 0.49, 0.2, 1), 3
        l, r, top, bot = X(cx - sw / 2), X(cx + sw / 2), Y(cy - sh / 2), Y(cy + sh / 2)
        mx, my = X(cx), Y(cy)
        if t in ('start', 'end', 'stop'):
            rad = (top - bot) / 2
            Color(*fill)
            RoundedRectangle(pos=(l, bot), size=(r - l, top - bot), radius=[rad])
            Color(*outline)
            Line(rounded_rectangle=(l, bot, r - l, top - bot, rad), width=ow)
        elif t in ('in', 'out'):
            s = 16
            poly([(l + s, top), (r, top), (r - s, bot), (l, bot)], fill, outline, ow)
        elif t in ('if', 'loop', 'post', 'select'):
            poly([(mx, top), (r, my), (mx, bot), (l, my)], fill, outline, ow)
        elif t == 'label':
            Color(*fill)
            Ellipse(pos=(l, bot), size=(r - l, top - bot))
            Color(*outline)
            Line(ellipse=(l, bot, r - l, top - bot), width=ow)
        elif t == 'case':
            s = 10
            poly([(l + s, top), (r - s, top), (r, my), (r - s, bot), (l + s, bot), (l, my)], fill, outline, ow)
        else:
            Color(*fill)
            Rectangle(pos=(l, bot), size=(r - l, top - bot))
            Color(*outline)
            Line(rectangle=(l, bot, r - l, top - bot), width=ow)
            if t == 'call':
                Line(points=[l + 9, top, l + 9, bot], width=1.3)
                Line(points=[r - 9, top, r - 9, bot], width=1.3)
        txt('\n'.join(lines), mx, my)

    def _export_dir(self):
        try:
            from android.storage import primary_external_storage_path
            d = os.path.join(primary_external_storage_path(), 'Download')
            os.makedirs(d, exist_ok=True)
            if os.access(d, os.W_OK):
                return d
        except Exception:
            pass
        return self.user_data_dir

    def export_chart(self, *a):
        if not self.prims:
            self.popup('Export', 'Nothing to export yet.')
            return
        base = os.path.join(self._export_dir(), 'flowchart_%s.' % time.strftime('%Y%m%d_%H%M%S'))
        try:
            img = self.chart.export_as_image()
            try:
                img.save(base + 'jpg')
                path = base + 'jpg'
            except Exception:
                img.save(base + 'png')
                path = base + 'png'
            self.popup('Exported', 'Saved to:\n%s' % path)
        except Exception as ex:
            self.popup('Export failed', str(ex))


if __name__ == '__main__':
    QBApp().run()
