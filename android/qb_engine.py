#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
QB ENGINE  -  QBasic interpreter + flowchart/DFD layout (no GUI; shared by the Android app)
  [1] CODE   : QBasic-style editor (syntax colouring, line numbers, F5 to run)
  [2] OUTPUT : 80x25 text screen (+ simple graphics) driven by a real QBasic interpreter
  [3] CHART  : live Flowchart / DFD Level-0 / DFD Level-1 generated from the typed program

Needs only the Python standard library (tkinter).  Run:   python qbasic_ide.py
"""
import sys, re, math, time, random, threading, queue, collections, os, datetime, textwrap, json

try:
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox
    import tkinter.font as tkfont
except Exception:                      # engine can still be imported / tested headless
    tk = None

# =====================================================================
#  ENGINE  -  errors, helpers, tokenizer, expression parser
# =====================================================================
ERR_CODES = {'Syntax error': 2, 'RETURN without GOSUB': 3, 'Out of DATA': 4,
             'Illegal function call': 5, 'Overflow': 6, 'Subscript out of range': 9,
             'Division by zero': 11, 'Type mismatch': 13, 'File not found': 53,
             'Bad file number': 52, 'Input past end of file': 62}


class QBError(Exception):
    def __init__(self, msg, code=None):
        Exception.__init__(self, msg)
        self.msg = msg
        self.code = code if code is not None else ERR_CODES.get(msg, 5)
        self.line = 0


class EndProgram(Exception):
    pass


class StopProgram(Exception):
    pass


class Dbl(float):
    """double-precision marker (prints with 15 digits)"""


TOK = re.compile(r'''\s*(?:
 (?P<num>&[Hh][0-9A-Fa-f]+&?|&[Oo][0-7]+&?|(?:\d+\.?\d*|\.\d+)(?:[EeDd][+-]?\d+)?[%&!#]?)
|(?P<str>"[^"]*"?)
|(?P<id>[A-Za-z_][A-Za-z0-9_.]*[$%&!#]?)
|(?P<op><>|<=|>=|=<|=>|[-+*/\\^=<>(),;:#])
)''', re.X)


def num_value(tok):
    u = tok.upper()
    if u.startswith('&H'):
        s = u[2:].rstrip('&')
        v = int(s, 16)
        if not u.endswith('&') and 0x7FFF < v <= 0xFFFF:
            v -= 0x10000
        return v
    if u.startswith('&O'):
        return int(u[2:].rstrip('&'), 8)
    suf = ''
    if u[-1] in '%&!#':
        suf, u = u[-1], u[:-1]
    dbl = ('D' in u) or suf == '#'
    u = u.replace('D', 'E')
    if suf in ('%', '&'):
        return int(round(float(u)))
    if '.' in u or 'E' in u or suf in ('!', '#'):
        f = float(u)
        return Dbl(f) if dbl else f
    return int(u)


def tokenize(s):
    pos, n, out = 0, len(s), []
    while pos < n:
        m = TOK.match(s, pos)
        if not m:
            if s[pos:].strip() == '':
                break
            raise QBError('Syntax error')
        pos = m.end()
        k = m.lastgroup
        v = m.group(k)
        if k == 'num':
            out.append(('num', num_value(v)))
        elif k == 'str':
            out.append(('str', v[1:-1] if v.endswith('"') and len(v) > 1 else v[1:]))
        else:
            out.append((k, v))
    return out


def split_q(text, sep, parens=False):
    out, cur, inq, depth = [], [], False, 0
    for ch in text:
        if ch == '"':
            inq = not inq
        if not inq:
            if parens:
                if ch == '(':
                    depth += 1
                elif ch == ')':
                    depth = max(0, depth - 1)
            if ch == sep and depth == 0:
                out.append(''.join(cur))
                cur = []
                continue
        cur.append(ch)
    out.append(''.join(cur))
    return out


_WORDCH = '_$%&!#.'


def strip_comment(line):
    inq = False
    for i, ch in enumerate(line):
        if ch == '"':
            inq = not inq
        elif inq:
            continue
        elif ch == "'":
            return line[:i]
        elif ch in 'rR' and line[i:i + 3].upper() == 'REM':
            before = line[i - 1] if i > 0 else ' '
            after = line[i + 3] if i + 3 < len(line) else ' '
            if not (before.isalnum() or before in _WORDCH) and not (after.isalnum() or after in '_'):
                return line[:i]
    return line


def find_kw(text, kw):
    inq, n, u = False, len(kw), text.upper()
    for i, ch in enumerate(text):
        if ch == '"':
            inq = not inq
            continue
        if inq:
            continue
        if u.startswith(kw, i):
            before = text[i - 1] if i > 0 else ' '
            after = text[i + n] if i + n < len(text) else ' '
            if not (before.isalnum() or before in _WORDCH) and not (after.isalnum() or after in _WORDCH):
                return i
    return -1


def fmt_num(v):
    if isinstance(v, bool):
        v = int(v)
    if isinstance(v, int):
        return str(v)
    if v != v:
        return 'NaN'
    if v in (float('inf'), float('-inf')):
        return 'Overflow'
    dbl = isinstance(v, Dbl)
    s = '%.*g' % (15 if dbl else 7, v)
    if 'e' in s:
        m, e = s.split('e')
        ex = int(e)
        s = '%s%s%s%02d' % (m, 'D' if dbl else 'E', '+' if ex >= 0 else '-', abs(ex))
    if s.startswith('0.'):
        s = s[1:]
    elif s.startswith('-0.'):
        s = '-' + s[2:]
    return s


def fmt_print(v):
    if isinstance(v, str):
        return v
    return (' ' if v >= 0 else '') + fmt_num(v) + ' '


def fmt_str(v):
    return (' ' if v >= 0 else '') + fmt_num(v)


VAL_RE = re.compile(r'^\s*([+-]?(?:\d+\.?\d*|\.\d+)(?:[EeDd][+-]?\d+)?)')


def val_fn(s):
    m = re.match(r'^\s*&[Hh]([0-9A-Fa-f]+)', s)
    if m:
        return int(m.group(1), 16)
    m = VAL_RE.match(s)
    if not m:
        return 0
    t = m.group(1).upper().replace('D', 'E')
    if '.' in t or 'E' in t:
        return float(t)
    return int(t)


NUMF = re.compile(r'(\+)?(\*\*\$|\*\*|\$\$)?([#,]*#(?:\.#*)?|\.#+)(\^\^\^\^)?([+-])?')


class _OutOfValues(Exception):
    pass


def using_num(v, m):
    if isinstance(v, str):
        raise QBError('Type mismatch')
    plus, pre, body, expo, trail = m.groups()
    width = len(m.group(0))
    dec = len(body.split('.')[1]) if '.' in body else 0
    if expo:
        s = '%.*E' % (dec, abs(v))
    elif ',' in body:
        s = ('{:,.%df}' % dec).format(abs(v))
    else:
        s = '%.*f' % (dec, abs(v))
    if body.startswith('.') and s.startswith('0.'):
        s = s[1:]
    neg = v < 0 and float(s.replace(',', '').replace('E', 'e')) != 0
    if pre in ('$$', '**$'):
        s = '$' + s
    if trail:
        s += ('-' if neg else ('+' if trail == '+' else ' '))
    elif plus:
        s = ('-' if neg else '+') + s
    elif neg:
        s = '-' + s
    if len(s) > width:
        return '%' + s
    return s.rjust(width, '*' if pre and pre.startswith('**') else ' ')


def using_format(fmt, vals):
    out, vi, n = [], 0, len(fmt)

    def nextval():
        nonlocal vi
        if vi >= len(vals):
            raise _OutOfValues()
        vi += 1
        return vals[vi - 1]

    try:
        while True:
            start_vi, i = vi, 0
            while i < n:
                ch = fmt[i]
                if ch == '_' and i + 1 < n:
                    out.append(fmt[i + 1]); i += 2; continue
                if ch == '!':
                    out.append(str(nextval())[:1]); i += 1; continue
                if ch == '&':
                    out.append(str(nextval())); i += 1; continue
                if ch == '\\':
                    j = fmt.find('\\', i + 1)
                    if j > 0:
                        w = j - i + 1
                        out.append(str(nextval())[:w].ljust(w)); i = j + 1; continue
                m = NUMF.match(fmt, i)
                if m and m.group(3):
                    out.append(using_num(nextval(), m)); i = m.end(); continue
                out.append(ch); i += 1
            if vi >= len(vals) or vi == start_vi:
                break
    except _OutOfValues:
        pass
    return ''.join(out)


class ExprParser:
    def __init__(self, toks):
        self.t, self.i = toks, 0

    def peek(self, o=0):
        j = self.i + o
        return self.t[j] if j < len(self.t) else (None, None)

    def next(self):
        tok = self.peek()
        self.i += 1
        return tok

    def end(self):
        return self.i >= len(self.t)

    def at_op(self, *ops):
        k, v = self.peek()
        return k == 'op' and v in ops

    def at_kw(self, *kws):
        k, v = self.peek()
        return k == 'id' and v.upper() in kws

    def expect(self, op):
        if not self.at_op(op):
            raise QBError('Syntax error')
        self.i += 1

    def parse(self):
        return self.p_imp()

    def _lvl(self, sub, kws):
        a = sub()
        while self.at_kw(*kws):
            op = self.next()[1].upper()
            a = ('bin', op, a, sub())
        return a

    def p_imp(self): return self._lvl(self.p_eqv, ('IMP',))
    def p_eqv(self): return self._lvl(self.p_xor, ('EQV',))
    def p_xor(self): return self._lvl(self.p_or, ('XOR',))
    def p_or(self): return self._lvl(self.p_and, ('OR',))
    def p_and(self): return self._lvl(self.p_not, ('AND',))

    def p_not(self):
        if self.at_kw('NOT'):
            self.next()
            return ('un', 'NOT', self.p_not())
        return self.p_cmp()

    def p_cmp(self):
        a = self.p_add()
        while True:
            k, v = self.peek()
            if k == 'op' and v in ('=', '<>', '<', '>', '<=', '>=', '=<', '=>'):
                self.next()
                v = {'=<': '<=', '=>': '>='}.get(v, v)
                a = ('bin', v, a, self.p_add())
            else:
                return a

    def p_add(self):
        a = self.p_mod()
        while self.at_op('+', '-'):
            op = self.next()[1]
            a = ('bin', op, a, self.p_mod())
        return a

    def p_mod(self): return self._lvl(self.p_idiv, ('MOD',))

    def p_idiv(self):
        a = self.p_mul()
        while self.at_op('\\'):
            self.next()
            a = ('bin', '\\', a, self.p_mul())
        return a

    def p_mul(self):
        a = self.p_neg()
        while self.at_op('*', '/'):
            op = self.next()[1]
            a = ('bin', op, a, self.p_neg())
        return a

    def p_neg(self):
        if self.at_op('-'):
            self.next()
            return ('un', '-', self.p_neg())
        if self.at_op('+'):
            self.next()
            return self.p_neg()
        return self.p_pow()

    def p_pow(self):
        a = self.p_atom()
        while self.at_op('^'):
            self.next()
            if self.at_op('-'):
                self.next()
                b = ('un', '-', self.p_atom())
            else:
                if self.at_op('+'):
                    self.next()
                b = self.p_atom()
            a = ('bin', '^', a, b)
        return a

    def p_atom(self):
        k, v = self.next()
        if k in ('num', 'str'):
            return (k, v)
        if k == 'op' and v == '(':
            e = self.parse()
            self.expect(')')
            return e
        if k == 'id':
            name = v.upper()
            if self.at_op('('):
                self.next()
                return ('idx', name, self.arglist())
            return ('var', name)
        raise QBError('Syntax error')

    def arglist(self):
        args = []
        if self.at_op(')'):
            self.next()
            return args
        while True:
            args.append(self.parse())
            if self.at_op(','):
                self.next()
                continue
            self.expect(')')
            return args


# =====================================================================
#  ENGINE  -  screen, arrays, statements
# =====================================================================
PAL = ['#000000', '#0000AA', '#00AA00', '#00AAAA', '#AA0000', '#AA00AA', '#AA5500', '#AAAAAA',
       '#555555', '#5555FF', '#55FF55', '#55FFFF', '#FF5555', '#FF55FF', '#FFFF55', '#FFFFFF']


class Screen:
    def __init__(self, rows=25, cols=80):
        self.rows, self.cols = rows, cols
        self.lock = threading.RLock()
        self.fg, self.bg = 7, 0
        self.bell = False
        self.clear()

    def clear(self):
        with self.lock:
            self.grid = [[(' ', self.fg, self.bg)] * self.cols for _ in range(self.rows)]
            self.r = self.c = 0
            self.dirty = True

    def color(self, fg=None, bg=None):
        if fg is not None:
            self.fg = int(fg) % 32 % 16
        if bg is not None:
            self.bg = int(bg) % 8

    def locate(self, r=None, c=None):
        with self.lock:
            if r is not None:
                self.r = min(max(int(r) - 1, 0), self.rows - 1)
            if c is not None:
                self.c = min(max(int(c) - 1, 0), self.cols - 1)

    def _nl(self):
        if self.r < self.rows - 1:
            self.r += 1
        else:
            self.grid.pop(0)
            self.grid.append([(' ', self.fg, self.bg)] * self.cols)

    def _put(self, ch):
        if ch == '\n':
            self.c = 0
            self._nl()
        elif ch == '\r':
            self.c = 0
        elif ch == '\b':
            self.c = max(0, self.c - 1)
        elif ch == '\t':
            for _ in range(8 - (self.c % 8)):
                self._put(' ')
        elif ch == '\x07':
            self.bell = True
        else:
            if self.c >= self.cols:
                self.c = 0
                self._nl()
            self.grid[self.r][self.c] = (ch, self.fg, self.bg)
            self.c += 1

    def write(self, s):
        with self.lock:
            for ch in s:
                self._put(ch)
            self.dirty = True

    def text(self):
        return '\n'.join(''.join(c[0] for c in row).rstrip() for row in self.grid).rstrip()


class QArr:
    def __init__(self, dims, default):
        self.dims, self.default, self.data = dims, default, {}

    def key(self, idx):
        if len(idx) != len(self.dims):
            raise QBError('Subscript out of range')
        for i, (lo, hi) in zip(idx, self.dims):
            if i < lo or i > hi:
                raise QBError('Subscript out of range')
        return tuple(idx)

    def get(self, idx):
        return self.data.get(self.key(idx), self.default)

    def set(self, idx, v):
        self.data[self.key(idx)] = v


class Stmt:
    __slots__ = ('kw', 'text', 'raw', 'line', 'jump', 'end', 'extra', 'h', 'block', 'cond', 'then')

    def __init__(self, kw, text, raw, line):
        self.kw, self.text, self.raw, self.line = kw, text, raw, line
        self.jump = self.end = self.extra = self.h = None
        self.block = False
        self.cond = self.then = ''


IDRE = re.compile(r'\s*([A-Za-z_][A-Za-z0-9_.]*[$%&!#]?|\?)')
TWO = {('END', 'IF'), ('END', 'SUB'), ('END', 'FUNCTION'), ('END', 'SELECT'), ('END', 'TYPE'),
       ('EXIT', 'FOR'), ('EXIT', 'DO'), ('EXIT', 'SUB'), ('EXIT', 'FUNCTION'),
       ('SELECT', 'CASE'), ('LINE', 'INPUT'), ('CASE', 'ELSE'), ('ON', 'ERROR')}
NOOPS = {'WIDTH', 'VIEW', 'WINDOW', 'PALETTE', 'PAINT', 'DRAW', 'PLAY', 'SOUND', 'KEY', 'TRON',
         'TROFF', 'DEFSEG', 'POKE', 'OUT', 'CLEAR', 'DECLARE'}
DEFTYPES = {'DEFINT': '%', 'DEFLNG': '&', 'DEFSNG': '!', 'DEFDBL': '#', 'DEFSTR': '$'}
TYPEMAP = {'STRING': '$', 'INTEGER': '%', 'LONG': '&', 'SINGLE': '!', 'DOUBLE': '#'}
FOR_RE = re.compile(r'^\s*([A-Za-z_][\w.]*[$%&!#]?)\s*=\s*(.+?)\s+TO\s+(.+?)(?:\s+STEP\s+(.+))?\s*$', re.I | re.S)
SCREEN_SIZES = {1: (320, 200), 2: (640, 200), 7: (320, 200), 8: (640, 200), 9: (640, 350),
                10: (640, 350), 11: (640, 480), 12: (640, 480), 13: (320, 200)}


def split_kw(stmt):
    m = IDRE.match(stmt)
    if not m:
        return None, stmt
    w = m.group(1).upper()
    rest = stmt[m.end():]
    m2 = IDRE.match(rest)
    if m2 and (w, m2.group(1).upper()) in TWO:
        return w + ' ' + m2.group(1).upper(), rest[m2.end():]
    return w, rest


class Interpreter:
    def __init__(self, io):
        self.io = io
        self.cache, self.tcache, self.acache = {}, {}, {}
        self.stmts, self.labels, self.data, self.data_labels = [], {}, [], {}
        self.subs, self.types, self.deffn = {}, {}, {}
        self.cur = None
        self.tagcache = {}
        self.reset()
        self._build_builtins()
        self.handler_words = {n[3:].split('_')[0] for n in dir(self) if n.startswith('st_')} | NOOPS | set(DEFTYPES)

    # ---------------------------------------------------------------- compile
    def compile(self, source):
        self.stmts, self.labels, self.data, self.data_labels = [], {}, [], {}
        self.subs, self.types, self.deffn = {}, {}, {}
        self.cache.clear(); self.acache.clear()
        self._in_type = None
        self.handler_words = {n[3:].split('_')[0] for n in dir(self) if n.startswith('st_')} | NOOPS | set(DEFTYPES)
        for ln, raw in enumerate(source.replace('\r', '').split('\n'), 1):
            try:
                line = strip_comment(raw).strip()
                if not line:
                    continue
                m = re.match(r'(\d+)\s*(.*)$', line)
                if m and self._in_type is None:
                    self._add_label(m.group(1))
                    line = m.group(2)
                else:
                    m = re.match(r'([A-Za-z][A-Za-z0-9_]*):(?!=)\s*(.*)$', line)
                    if m and m.group(1).upper() not in self.handler_words and self._in_type is None:
                        self._add_label(m.group(1).upper())
                        line = m.group(2)
                if not line:
                    continue
                for part in self._split_line(line):
                    self._add_stmt(part, ln)
            except QBError as e:
                e.line = ln
                raise
        if self._in_type is not None:
            raise QBError('Block not closed: TYPE')
        self._link()

    def _add_label(self, name):
        self.labels[name.upper()] = len(self.stmts)
        self.data_labels[name.upper()] = len(self.data)

    def _split_line(self, line):
        m = IDRE.match(line)
        if m and m.group(1).upper() == 'IF':
            i = find_kw(line, 'THEN')
            if i >= 0 and line[i + 4:].strip():
                return [line]
        return [p for p in split_q(line, ':') if p.strip()]

    def _add_stmt(self, text, ln):
        text = text.strip()
        kw, rest = split_kw(text)
        if kw is None:
            raise QBError('Syntax error')
        if self._in_type is not None:
            if kw == 'END TYPE':
                self._in_type = None
            else:
                m = re.match(r'^\s*([\w.]+)\s+AS\s+(\w+)', text, re.I)
                if not m:
                    raise QBError('Syntax error')
                self.types[self._in_type].append((m.group(1).upper(), TYPEMAP.get(m.group(2).upper(), '!')))
            return
        if kw == 'REM':
            return
        if kw == 'TYPE':
            self._in_type = rest.strip().upper()
            self.types[self._in_type] = []
            return
        if kw == 'DECLARE':
            return
        if kw == 'DATA':
            for item in split_q(rest, ','):
                it = item.strip()
                if it.startswith('"'):
                    self.data.append((it[1:-1] if it.endswith('"') and len(it) > 1 else it[1:], True))
                else:
                    self.data.append((it, False))
            return
        if kw == 'DEF':
            m = re.match(r'^\s*(FN[\w.]*[$%&!#]?)\s*(?:\(([^)]*)\))?\s*=\s*(.+)$', rest, re.I | re.S)
            if not m:
                raise QBError('Syntax error')
            params = [p.strip().upper() for p in (m.group(2) or '').split(',') if p.strip()]
            self.deffn[m.group(1).upper()] = (params, self.E(m.group(3)))
            return
        s = Stmt(kw, rest.strip(), text, ln)
        if kw in ('IF', 'ELSEIF'):
            i = find_kw(rest, 'THEN')
            if i < 0:
                raise QBError('Syntax error')
            s.cond = rest[:i].strip()
            s.then = rest[i + 4:].strip()
            s.block = (kw == 'ELSEIF') or s.then == ''
            self.E(s.cond)
        self.stmts.append(s)
        if kw == 'ELSE' and s.text:
            s.text = ''
            for part in self._split_line(rest.strip()):
                self._add_stmt(part, ln)

    def _link(self):
        S, st, fix = self.stmts, [], []

        def err(msg, i):
            e = QBError(msg)
            e.line = S[i].line
            return e

        for i, s in enumerate(S):
            k = s.kw
            if k == 'IF' and s.block:
                st.append(['IF', i, [i]])
            elif k in ('ELSEIF', 'ELSE'):
                if not st or st[-1][0] != 'IF':
                    raise err('ELSE without IF', i)
                st[-1][2].append(i)
            elif k == 'END IF':
                if not st or st[-1][0] != 'IF':
                    raise err('END IF without IF', i)
                br = st.pop()[2] + [i]
                for a, b in zip(br, br[1:]):
                    S[a].jump = b
                for a in br[:-1]:
                    S[a].end = i
            elif k == 'FOR':
                st.append(['FOR', i])
            elif k == 'NEXT':
                n = len([x for x in split_q(s.text, ',') if x.strip()]) or 1
                idxs = []
                for _ in range(n):
                    if not st or st[-1][0] != 'FOR':
                        raise err('NEXT without FOR', i)
                    f = st.pop()[1]
                    S[f].jump = i
                    idxs.append(f)
                s.extra = idxs
            elif k == 'WHILE':
                st.append(['WHILE', i])
            elif k == 'WEND':
                if not st or st[-1][0] != 'WHILE':
                    raise err('WEND without WHILE', i)
                w = st.pop()[1]
                S[w].jump = i
                s.jump = w
            elif k == 'DO':
                st.append(['DO', i])
            elif k == 'LOOP':
                if not st or st[-1][0] != 'DO':
                    raise err('LOOP without DO', i)
                d = st.pop()[1]
                S[d].jump = i
                s.jump = d
            elif k == 'SELECT CASE':
                st.append(['SELECT', i, []])
            elif k in ('CASE', 'CASE ELSE'):
                if not st or st[-1][0] != 'SELECT':
                    raise err('CASE without SELECT', i)
                st[-1][2].append(i)
            elif k == 'END SELECT':
                if not st or st[-1][0] != 'SELECT':
                    raise err('END SELECT without SELECT', i)
                _, si, cases = st.pop()
                S[si].extra = cases
                S[si].jump = i
                for c in cases:
                    S[c].end = i
            elif k in ('SUB', 'FUNCTION'):
                st.append(['SUB', i])
            elif k in ('END SUB', 'END FUNCTION'):
                if not st or st[-1][0] != 'SUB':
                    raise err(k + ' without ' + k[4:], i)
                b = st.pop()[1]
                S[b].jump = i
                self._register_sub(S[b], b, i)
            elif k in ('EXIT FOR', 'EXIT DO'):
                kind = k[5:]
                for x in reversed(st):
                    if x[0] == kind:
                        fix.append((i, x[1]))
                        break
                else:
                    raise err(k + ' not within a loop', i)
        if st:
            raise err('Block not closed: ' + st[-1][0], st[-1][1])
        for i, target in fix:
            S[i].jump = S[target].jump + 1
            S[i].extra = target

    def _register_sub(self, s, idx, end):
        m = re.match(r'^\s*([A-Za-z_][\w.]*[$%&!#]?)\s*(?:\((.*)\))?\s*(?:STATIC)?\s*$', s.text, re.I | re.S)
        if not m:
            raise QBError('Syntax error')
        name = m.group(1).upper()
        params = []
        for p in split_q(m.group(2) or '', ',', True):
            p = p.strip()
            if not p:
                continue
            pm = re.match(r'^([A-Za-z_][\w.]*[$%&!#]?)\s*(\(\s*\))?', p)
            params.append((pm.group(1).upper(), bool(pm.group(2))))
        info = {'name': name, 'kind': s.kw, 'idx': idx, 'end': end, 'params': params}
        self.subs[name] = info
        if name[-1] in '$%&!#':
            self.subs.setdefault(name[:-1], info)

    # ---------------------------------------------------------------- state
    def reset(self):
        self.globals, self.arrays, self.shared, self.frames = {}, {}, set(), []
        self.var_types = {}
        self.deftype = {chr(c): '!' for c in range(65, 91)}
        self.pc = 0
        self.loop_stack, self.gosub_stack = [], []
        self.data_ptr, self.base = 0, 0
        self.files = {}
        self.rng = random.Random(1)
        self.last_rnd = 0.5
        self.err_handler, self.err_next, self.in_handler = None, False, False
        self.err = self.erl = self.resume_pc = 0
        self.lastxy, self.gcolor = (0, 0), 15
        self.last_frame = None
        self.select_vals = {}

    # ---------------------------------------------------------------- caches
    def E(self, text):
        n = self.cache.get(text)
        if n is None:
            p = ExprParser(tokenize(text))
            n = p.parse()
            if not p.end():
                raise QBError('Syntax error')
            self.cache[text] = n
        return n

    def tok(self, text):
        t = self.tcache.get(text)
        if t is None:
            t = self.tcache[text] = tokenize(text)
        return t

    def args_opt(self, text):
        r = self.acache.get(('O', text))
        if r is None:
            p = ExprParser(self.tok(text))
            r = []
            if p.t:
                while True:
                    r.append(None if (p.at_op(',') or p.end()) else p.parse())
                    if p.at_op(','):
                        p.next()
                        continue
                    break
                if not p.end():
                    raise QBError('Syntax error')
            self.acache[('O', text)] = r
        return r

    # ---------------------------------------------------------------- variables
    def type_of(self, name):
        c = name[-1]
        if c in '$%&!#':
            return c
        return self.var_types.get(name) or self.deftype.get(name[0], '!')

    def default(self, name):
        t = self.type_of(name)
        return '' if t == '$' else (Dbl(0) if t == '#' else 0)

    def coerce(self, name, v):
        t = self.type_of(name)
        if t == '$':
            if not isinstance(v, str):
                raise QBError('Type mismatch')
            return v
        if isinstance(v, str):
            raise QBError('Type mismatch')
        if t == '%':
            v = int(round(v))
            if not -32768 <= v <= 32767:
                raise QBError('Overflow')
            return v
        if t == '&':
            v = int(round(v))
            if not -2147483648 <= v <= 2147483647:
                raise QBError('Overflow')
            return v
        if t == '#':
            return v if isinstance(v, Dbl) else Dbl(v)
        return float(v) if isinstance(v, float) else v

    def scope_vars(self, name):
        if self.frames:
            fr = self.frames[-1]
            if name in fr['vars']:
                return fr['vars']
            if name in fr['shared'] or name in self.shared or fr.get('all'):
                return self.globals
            return fr['vars']
        return self.globals

    def scope_arrays(self, name):
        if self.frames:
            fr = self.frames[-1]
            if name in fr['arrays']:
                return fr['arrays']
            if name in fr['shared'] or name in self.shared or fr.get('all'):
                return self.arrays
            return fr['arrays']
        return self.arrays

    def get_var(self, name):
        d = self.scope_vars(name)
        if name in d:
            return d[name]
        z = self.zerofn.get(name)
        if z:
            return z()
        return self.default(name)

    def set_var(self, name, v):
        self.scope_vars(name)[name] = self.coerce(name, v)

    def get_arr(self, name, nd):
        arrs = self.scope_arrays(name)
        a = arrs.get(name)
        if a is None:
            a = arrs[name] = QArr([(self.base, 10)] * nd, self.default(name))
        return a

    def toint(self, v):
        if isinstance(v, str):
            raise QBError('Type mismatch')
        return int(round(v))

    def assign_to(self, name, args, v):
        if args is None:
            self.set_var(name, v)
        elif name == 'MID$':
            raise QBError('Syntax error')
        else:
            arr = self.get_arr(name, len(args))
            arr.set([self.toint(self.ev(a)) for a in args], self.coerce(name, v))

    def assign_node(self, node, v):
        if node[0] == 'var':
            self.set_var(node[1], v)
        elif node[0] == 'idx':
            self.assign_to(node[1], node[2], v)
        else:
            raise QBError('Syntax error')

    # ---------------------------------------------------------------- expressions
    def truth(self, v):
        if isinstance(v, str):
            raise QBError('Type mismatch')
        return v != 0

    def ev(self, n):
        k = n[0]
        if k == 'num' or k == 'str':
            return n[1]
        if k == 'var':
            return self.get_var(n[1])
        if k == 'bin':
            return self.binop(n[1], self.ev(n[2]), self.ev(n[3]))
        if k == 'un':
            v = self.ev(n[2])
            if isinstance(v, str):
                raise QBError('Type mismatch')
            return -v if n[1] == '-' else ~int(round(v))
        if k == 'idx':
            return self.call_or_index(n[1], n[2])
        raise QBError('Syntax error')

    def binop(self, op, a, b):
        sa, sb = isinstance(a, str), isinstance(b, str)
        if op == '+':
            if sa and sb:
                return a + b
            if sa or sb:
                raise QBError('Type mismatch')
            return self._d(a + b, a, b)
        if op in ('=', '<>', '<', '>', '<=', '>='):
            if sa != sb:
                raise QBError('Type mismatch')
            r = {'=': a == b, '<>': a != b, '<': a < b, '>': a > b, '<=': a <= b, '>=': a >= b}[op]
            return -1 if r else 0
        if sa or sb:
            raise QBError('Type mismatch')
        if op == '-':
            return self._d(a - b, a, b)
        if op == '*':
            return self._d(a * b, a, b)
        if op == '/':
            if b == 0:
                raise QBError('Division by zero')
            return self._d(a / b, a, b)
        if op == '\\':
            x, y = int(round(a)), int(round(b))
            if y == 0:
                raise QBError('Division by zero')
            q = abs(x) // abs(y)
            return q if (x >= 0) == (y >= 0) else -q
        if op == 'MOD':
            x, y = int(round(a)), int(round(b))
            if y == 0:
                raise QBError('Division by zero')
            return x - y * int(x / y)
        if op == '^':
            try:
                return self._d(math.pow(a, b), a, b)
            except ZeroDivisionError:
                raise QBError('Division by zero')
            except OverflowError:
                raise QBError('Overflow')
            except ValueError:
                raise QBError('Illegal function call')
        x, y = int(round(a)), int(round(b))
        if op == 'AND': return x & y
        if op == 'OR': return x | y
        if op == 'XOR': return x ^ y
        if op == 'EQV': return ~(x ^ y)
        if op == 'IMP': return (~x) | y
        raise QBError('Syntax error')

    @staticmethod
    def _d(r, a, b):
        if isinstance(r, float) and (isinstance(a, Dbl) or isinstance(b, Dbl)):
            return Dbl(r)
        return r

    def call_or_index(self, name, argnodes):
        info = self.subs.get(name) or (self.subs.get(name[:-1]) if name[-1:] in '$%&!#' else None)
        if info and info['kind'] == 'FUNCTION':
            return self.invoke(info['name'], argnodes)
        if name in self.deffn:
            return self.call_deffn(name, argnodes)
        arrs = self.scope_arrays(name)
        if name not in arrs:
            if name in ('UBOUND', 'LBOUND'):
                an = argnodes[0][1]
                dim = self.toint(self.ev(argnodes[1])) if len(argnodes) > 1 else 1
                arr = self.scope_arrays(an).get(an)
                if arr is None or not 1 <= dim <= len(arr.dims):
                    raise QBError('Subscript out of range')
                return arr.dims[dim - 1][0 if name == 'LBOUND' else 1]
            f = self.builtins.get(name)
            if f:
                try:
                    return f(*[self.ev(a) for a in argnodes])
                except TypeError:
                    raise QBError('Type mismatch')
                except (ValueError, OverflowError, ZeroDivisionError, IndexError):
                    raise QBError('Illegal function call')
        arr = self.get_arr(name, len(argnodes))
        return arr.get([self.toint(self.ev(a)) for a in argnodes])

    def call_deffn(self, name, argnodes):
        params, body = self.deffn[name]
        if len(params) != len(argnodes):
            raise QBError('Argument-count mismatch')
        vals = [self.ev(a) for a in argnodes]
        fr = {'vars': dict(zip(params, vals)), 'arrays': {}, 'shared': set(), 'all': True}
        self.frames.append(fr)
        try:
            return self.ev(body)
        finally:
            self.frames.pop()

    def invoke(self, name, argnodes):
        sub = self.subs.get(name)
        if sub is None:
            raise QBError('Subprogram not defined')
        params = sub['params']
        if len(params) != len(argnodes):
            raise QBError('Argument-count mismatch')
        fr = {'vars': {}, 'arrays': {}, 'shared': set(), 'refs': []}
        for (pn, is_arr), an in zip(params, argnodes):
            if is_arr:
                src = an[1] if an and an[0] in ('idx', 'var') else None
                arr = self.scope_arrays(src).get(src) if src else None
                if arr is None:
                    raise QBError('Parameter type mismatch')
                fr['arrays'][pn] = arr
            else:
                val = self.ev(an)
                fr['vars'][pn] = self.coerce(pn, val)
                if an[0] == 'var' and an[1] not in self.zerofn:
                    fr['refs'].append((pn, an))
                elif an[0] == 'idx' and an[1] in self.scope_arrays(an[1]):
                    fr['refs'].append((pn, an))
        if sub['kind'] == 'FUNCTION':
            fr['vars'][sub['name']] = self.default(sub['name'])
        saved = (self.pc, self.loop_stack, self.gosub_stack)
        self.loop_stack, self.gosub_stack = [], []
        self.frames.append(fr)
        depth = len(self.frames)
        self.pc = sub['idx'] + 1
        if self.io.live:
            self.io.trace(('L%d' % self.stmts[sub['idx']].line,))
        try:
            self.run_loop(depth)
        finally:
            self.pc, self.loop_stack, self.gosub_stack = saved
        ret = self.last_frame if self.last_frame is not None else fr
        for pn, node in fr['refs']:
            if pn in ret['vars']:
                try:
                    self.assign_node(node, ret['vars'][pn])
                except QBError:
                    pass
        if sub['kind'] == 'FUNCTION':
            full = sub['name']
            base = full[:-1] if full[-1] in '$%&!#' else full
            v = ret['vars'].get(full, ret['vars'].get(base, self.default(full)))
            return self.coerce(full, v)
        return None

    def _return(self):
        if not self.frames:
            raise QBError('RETURN without GOSUB')
        self.last_frame = self.frames.pop()

    # ---------------------------------------------------------------- run loop
    def run(self):
        self.reset()
        try:
            if self.io.live:
                self.io.trace(('START',))
            self.run_loop(0)
        except EndProgram:
            pass
        except StopProgram:
            self.io.out('\n[program stopped]')
        except QBError as e:
            line = self.cur.line if self.cur else 0
            self.io.error('%s in line %d' % (e.msg, line), line)
        finally:
            for f in self.files.values():
                if f.get('f'):
                    try:
                        f['f'].close()
                    except Exception:
                        pass
            self.files = {}

    def run_loop(self, min_frames):
        S = self.stmts
        while self.pc < len(S) and len(self.frames) >= min_frames:
            if self.io.stopped:
                raise StopProgram()
            s = S[self.pc]
            self.cur = s
            self.pc += 1
            if self.io.live:
                self.io.trace(self.tags_for(s))
            try:
                self.exec(s)
            except QBError as e:
                if not self.handle_error(e, s):
                    raise

    def tags_for(self, s):
        t = self.tagcache.get(id(s))
        if t is None:
            if s.kw == 'FOR':
                t = ('I%d' % s.line, 'L%d' % s.line)
            elif s.kw == 'NEXT':
                t = ('N%d' % s.line,) + tuple('L%d' % self.stmts[f].line for f in s.extra)
            else:
                t = ('L%d' % s.line,)
            self.tagcache[id(s)] = t
        return t

    def handle_error(self, e, s):
        if self.err_next:
            return True
        if self.err_handler is not None and not self.in_handler:
            self.err, self.erl = e.code, s.line
            self.resume_pc = self.pc - 1
            self.in_handler = True
            self.pc = self.err_handler
            return True
        return False

    def exec(self, s):
        h = s.h
        if h is None:
            if s.kw in NOOPS:
                h = self.st_noop
            elif s.kw in DEFTYPES:
                h = self.st_deftype
            else:
                h = getattr(self, 'st_' + s.kw.replace(' ', '_').replace('$', 'S').replace('?', 'Q'), None) \
                    or self.assign_or_call
            s.h = h
        h(s)

    def make_stmt(self, text):
        key = ('S', text)
        s = self.acache.get(key)
        if s is None:
            kw, rest = split_kw(text.strip())
            if kw is None:
                raise QBError('Syntax error')
            s = Stmt(kw, rest.strip(), text.strip(), self.cur.line if self.cur else 0)
            if kw in ('IF', 'ELSEIF'):
                i = find_kw(rest, 'THEN')
                if i < 0:
                    raise QBError('Syntax error')
                s.cond, s.then = rest[:i].strip(), rest[i + 4:].strip()
            if kw in ('EXIT FOR', 'EXIT DO'):      # position dependent: never cached
                t = self.enclosing(kw[5:], self.pc - 1)
                s.jump, s.extra = self.stmts[t].jump + 1, t
                return s
            self.acache[key] = s
        return s

    def enclosing(self, kind, idx):
        end_kw, depth = ('NEXT' if kind == 'FOR' else 'LOOP'), 0
        for i in range(idx - 1, -1, -1):
            k = self.stmts[i].kw
            if k == end_kw:
                depth += len(self.stmts[i].extra) if kind == 'FOR' else 1
            elif k == kind:
                if depth == 0:
                    return i
                depth -= 1
        raise QBError('EXIT not within a loop')

    def exec_inline(self, text):
        text = text.strip()
        if not text:
            return
        if re.fullmatch(r'\d+', text):
            return self.st_GOTO(Stmt('GOTO', text, text, 0))
        start = self.pc
        for part in split_q(text, ':'):
            if part.strip():
                self.exec(self.make_stmt(part))
                if self.pc != start:
                    break

    # ---------------------------------------------------------------- statements: basics
    def st_noop(self, s): pass
    def st_REM(self, s): pass
    def st_END_IF(self, s): pass
    def st_END_SELECT(self, s): pass
    def st_END_TYPE(self, s): pass
    def st_STATIC(self, s): pass
    def st_TYPE(self, s): pass

    def st_LET(self, s):
        self.assign_or_call(Stmt('', s.text, s.text, s.line))

    def assign_or_call(self, s):
        key = ('A', s.raw)
        r = self.acache.get(key)
        if r is None:
            p = ExprParser(self.tok(s.raw))
            r = ('none',)
            k, v = p.peek()
            if k == 'id':
                p.next()
                name, args = v.upper(), None
                if p.at_op('('):
                    p.next()
                    args = p.arglist()
                if p.at_op('='):
                    p.next()
                    e = p.parse()
                    if not p.end():
                        raise QBError('Syntax error')
                    r = ('assign', name, args, e)
            self.acache[key] = r
        if r[0] == 'assign':
            self.assign_to(r[1], r[2], self.ev(r[3]))
            return
        m = re.match(r'^\s*([A-Za-z_][\w.]*[$%&!#]?)\s*(.*)$', s.raw, re.S)
        if m and (m.group(1).upper() in self.subs or m.group(1).upper()[:-1] in self.subs):
            self.call_text(m.group(1).upper(), m.group(2))
            return
        raise QBError('Syntax error')

    def call_text(self, name, rest):
        rest = rest.strip()
        if rest.startswith('(') and rest.endswith(')') and len(split_q(rest[1:-1], ',', True)) == len(split_q(rest, ',', True)):
            rest = rest[1:-1]
        nodes = self.args_opt(rest)
        if any(n is None for n in nodes):
            raise QBError('Syntax error')
        info = self.subs.get(name) or self.subs.get(name[:-1])
        if info is None:
            raise QBError('Subprogram not defined')
        self.invoke(info['name'], nodes)

    def st_CALL(self, s):
        m = re.match(r'^\s*([A-Za-z_][\w.]*[$%&!#]?)\s*(.*)$', s.text, re.S)
        if not m:
            raise QBError('Syntax error')
        self.call_text(m.group(1).upper(), m.group(2))

    def st_SUB(self, s): self.pc = s.jump + 1
    st_FUNCTION = st_SUB
    def st_END_SUB(self, s): self._return()
    st_END_FUNCTION = st_END_SUB
    st_EXIT_SUB = st_END_SUB
    st_EXIT_FUNCTION = st_END_SUB

    def st_SHARED(self, s):
        names = [t[1].upper() for t in self.tok(s.text) if t[0] == 'id' and t[1].upper() != 'AS']
        if self.frames:
            self.frames[-1]['shared'].update(names)
        else:
            self.shared.update(names)

    def st_COMMON(self, s):
        self.shared.update(t[1].upper() for t in self.tok(re.sub(r'(?i)^\s*SHARED', '', s.text))
                           if t[0] == 'id')

    def st_deftype(self, s):
        t = DEFTYPES[s.kw]
        for rng in s.text.split(','):
            rng = rng.strip().upper()
            if not rng:
                continue
            a, _, b = rng.partition('-')
            for c in range(ord(a[0]), ord((b or a)[0]) + 1):
                self.deftype[chr(c)] = t

    def st_OPTION(self, s):
        m = re.match(r'(?i)\s*BASE\s+(\d)', s.text)
        if m:
            self.base = int(m.group(1))

    def st_END(self, s): raise EndProgram()
    st_STOP = st_END
    st_SYSTEM = st_END

    # ---------------------------------------------------------------- control flow
    def st_IF(self, s):
        c = self.truth(self.ev(self.E(s.cond)))
        if s.block:
            if not c:
                self.skip_branch(s)
            return
        if s.extra is None:
            i = find_kw(s.then, 'ELSE')
            s.extra = (s.then, '') if i < 0 else (s.then[:i], s.then[i + 4:])
        self.exec_inline(s.extra[0] if c else s.extra[1])

    def skip_branch(self, s):
        j = s.jump
        while True:
            t = self.stmts[j]
            if t.kw == 'ELSEIF':
                if self.io.live:
                    self.io.trace(('L%d' % t.line,))
                if self.truth(self.ev(self.E(t.cond))):
                    self.pc = j + 1
                    return
                j = t.jump
                continue
            self.pc = j + 1
            return

    def st_ELSEIF(self, s): self.pc = s.end
    st_ELSE = st_ELSEIF

    def st_FOR(self, s):
        if s.extra is None:
            m = FOR_RE.match(s.text)
            if not m:
                raise QBError('Syntax error')
            s.extra = (m.group(1).upper(), self.E(m.group(2)), self.E(m.group(3)),
                       self.E(m.group(4)) if m.group(4) else None)
        var, a, b, c = s.extra
        start, end = self.ev(a), self.ev(b)
        step = self.ev(c) if c else 1
        self.set_var(var, start)
        fidx = self.pc - 1
        self.loop_stack = [e for e in self.loop_stack if e[1] != fidx]
        if (step > 0 and start > end) or (step < 0 and start < end):
            self.pc = s.jump + 1
            return
        self.loop_stack.append(['FOR', fidx, var, end, step])

    def st_NEXT(self, s):
        for fidx in s.extra:
            ent = None
            for k in range(len(self.loop_stack) - 1, -1, -1):
                if self.loop_stack[k][1] == fidx:
                    ent = self.loop_stack[k]
                    del self.loop_stack[k + 1:]
                    break
            if ent is None:
                raise QBError('NEXT without FOR')
            _, _, var, end, step = ent
            self.set_var(var, self.get_var(var) + step)
            v = self.get_var(var)
            if (step > 0 and v <= end) or (step < 0 and v >= end):
                self.pc = fidx + 1
                return
            self.loop_stack.pop()

    def st_EXIT_FOR(self, s):
        while self.loop_stack:
            if self.loop_stack.pop()[1] == s.extra:
                break
        self.pc = s.jump

    def st_EXIT_DO(self, s): self.pc = s.jump

    def st_WHILE(self, s):
        if not self.truth(self.ev(self.E(s.text))):
            self.pc = s.jump + 1

    def st_WEND(self, s): self.pc = s.jump

    def _dl(self, s):
        if s.extra is None:
            m = re.match(r'(?i)^\s*(WHILE|UNTIL)\b(.*)$', s.text, re.S)
            s.extra = (m.group(1).upper(), self.E(m.group(2))) if m else ('', None)
        return s.extra

    def st_DO(self, s):
        kind, cond = self._dl(s)
        if kind:
            v = self.truth(self.ev(cond))
            if not (v if kind == 'WHILE' else not v):
                self.pc = s.jump + 1

    def st_LOOP(self, s):
        kind, cond = self._dl(s)
        if not kind:
            self.pc = s.jump
            return
        v = self.truth(self.ev(cond))
        if v if kind == 'WHILE' else not v:
            self.pc = s.jump + 1

    def st_SELECT_CASE(self, s):
        v = self.ev(self.E(s.text))
        for ci in s.extra:
            if self.case_match(self.stmts[ci], v):
                if self.io.live:
                    self.io.trace(('L%d' % self.stmts[ci].line,))
                self.pc = ci + 1
                return
        self.pc = s.jump + 1

    def case_match(self, c, v):
        if c.kw == 'CASE ELSE':
            return True
        if not c.cond:
            p = ExprParser(self.tok(c.text))
            items = []
            while True:
                if p.at_kw('IS'):
                    p.next()
                    op = p.next()[1]
                    items.append(('is', {'=<': '<=', '=>': '>='}.get(op, op), p.parse()))
                else:
                    a = p.parse()
                    if p.at_kw('TO'):
                        p.next()
                        items.append(('range', a, p.parse()))
                    else:
                        items.append(('val', a))
                if p.at_op(','):
                    p.next()
                    continue
                break
            c.cond = items
        for it in c.cond:
            try:
                if it[0] == 'val':
                    if self.ev(it[1]) == v:
                        return True
                elif it[0] == 'range':
                    if self.ev(it[1]) <= v <= self.ev(it[2]):
                        return True
                else:
                    if self.binop(it[1], v, self.ev(it[2])) != 0:
                        return True
            except TypeError:
                raise QBError('Type mismatch')
        return False

    def st_CASE(self, s): self.pc = s.end
    st_CASE_ELSE = st_CASE

    def label(self, name):
        u = name.strip().upper()
        if u not in self.labels:
            raise QBError('Label not defined')
        return self.labels[u]

    def st_GOTO(self, s): self.pc = self.label(s.text)

    def st_GOSUB(self, s):
        tgt = self.label(s.text)
        self.gosub_stack.append(self.pc)
        self.pc = tgt

    def st_RETURN(self, s):
        if not self.gosub_stack:
            raise QBError('RETURN without GOSUB')
        self.pc = self.gosub_stack.pop()

    def st_ON(self, s):
        m = re.match(r'^(.+?)\s+(GOTO|GOSUB)\s+(.+)$', s.text, re.I | re.S)
        if not m:
            raise QBError('Syntax error')
        n = self.toint(self.ev(self.E(m.group(1))))
        labs = [x.strip() for x in m.group(3).split(',')]
        if 1 <= n <= len(labs):
            if m.group(2).upper() == 'GOSUB':
                self.gosub_stack.append(self.pc)
            self.pc = self.label(labs[n - 1])

    def st_ON_ERROR(self, s):
        t = s.text.strip().upper()
        if t.startswith('RESUME'):
            self.err_next = True
        elif t.startswith('GOTO'):
            lab = t[4:].strip()
            self.err_next = False
            self.err_handler = None if lab in ('0', '') else self.label(lab)

    def st_RESUME(self, s):
        t = s.text.strip().upper()
        self.in_handler = False
        if t == 'NEXT':
            self.pc = self.resume_pc + 1
        elif t == '':
            self.pc = self.resume_pc
        else:
            self.pc = self.label(t)

    def st_ERROR(self, s):
        n = self.toint(self.ev(self.E(s.text)))
        raise QBError('Unprintable error', n)

    # ---------------------------------------------------------------- data / variables
    def st_DIM(self, s, redim=False):
        text, shared = s.text, False
        if re.match(r'(?i)SHARED\b', text):
            shared, text = True, text[6:]
        p = ExprParser(self.tok(text))
        while not p.end():
            k, v = p.next()
            if k != 'id':
                raise QBError('Syntax error')
            name, dims = v.upper(), None
            if p.at_op('('):
                p.next()
                dims = []
                while True:
                    a = self.toint(self.ev(p.parse()))
                    if p.at_kw('TO'):
                        p.next()
                        dims.append((a, self.toint(self.ev(p.parse()))))
                    else:
                        dims.append((self.base, a))
                    if p.at_op(','):
                        p.next()
                        continue
                    p.expect(')')
                    break
            typ = None
            if p.at_kw('AS'):
                p.next()
                typ = p.next()[1].upper()
                if p.at_op('*'):
                    p.next(); p.next()
            if p.at_op(','):
                p.next()
            if typ in TYPEMAP:
                self.var_types[name] = TYPEMAP[typ]
            if shared and not self.frames:
                self.shared.add(name)
            if dims is not None:
                self.scope_arrays(name)[name] = QArr(dims, self.default(name))
            elif typ in self.types:
                for fname, ft in self.types[typ]:
                    key = '%s.%s' % (name, fname)
                    self.var_types[key] = ft
                    self.scope_vars(key)[key] = self.default(key)
                    if shared and not self.frames:
                        self.shared.add(key)
            else:
                self.scope_vars(name)[name] = self.default(name)

    st_REDIM = st_DIM

    def st_ERASE(self, s):
        for t in self.tok(s.text):
            if t[0] == 'id':
                self.scope_arrays(t[1].upper()).pop(t[1].upper(), None)

    def st_CONST(self, s):
        p = ExprParser(self.tok(s.text))
        while not p.end():
            name = p.next()[1].upper()
            p.expect('=')
            v = self.ev(p.parse())
            self.globals[name] = v
            self.shared.add(name)
            if p.at_op(','):
                p.next()

    def st_READ(self, s):
        p = ExprParser(self.tok(s.text))
        while not p.end():
            node = p.p_atom()
            if self.data_ptr >= len(self.data):
                raise QBError('Out of DATA')
            txt, quoted = self.data[self.data_ptr]
            self.data_ptr += 1
            name = node[1]
            if self.type_of(name) == '$':
                v = txt
            else:
                try:
                    v = float(txt) if txt.strip() else 0
                    if v == int(v) and 'E' not in txt.upper() and '.' not in txt:
                        v = int(v)
                except ValueError:
                    raise QBError('Syntax error')
            self.assign_node(node, v)
            if p.at_op(','):
                p.next()

    def st_RESTORE(self, s):
        t = s.text.strip().upper()
        self.data_ptr = self.data_labels.get(t, 0) if t else 0

    def st_SWAP(self, s):
        p = ExprParser(self.tok(s.text))
        a = p.p_atom()
        p.expect(',')
        b = p.p_atom()
        va, vb = self.ev(a), self.ev(b)
        self.assign_node(a, vb)
        self.assign_node(b, va)

    def st_RANDOMIZE(self, s):
        t = s.text.strip()
        if t and t.upper() != 'TIMER':
            self.rng = random.Random(self.ev(self.E(t)))
        else:
            self.rng = random.Random(time.time())

    # ---------------------------------------------------------------- screen
    def st_CLS(self, s):
        self.io.cls()

    def st_COLOR(self, s):
        a = [None if n is None else self.toint(self.ev(n)) for n in self.args_opt(s.text)]
        a += [None] * (3 - len(a))
        if a[0] is not None:
            self.gcolor = a[0] % 16
        self.io.screen.color(a[0], a[1])

    def st_LOCATE(self, s):
        a = [None if n is None else self.toint(self.ev(n)) for n in self.args_opt(s.text)]
        a += [None] * (2 - len(a))
        self.io.screen.locate(a[0], a[1])

    def st_BEEP(self, s): self.io.beep()

    def st_SLEEP(self, s):
        t = s.text.strip()
        self.io.sleep(self.ev(self.E(t)) if t else 0)

    def st_SCREEN(self, s):
        a = self.args_opt(s.text)
        n = self.toint(self.ev(a[0])) if a and a[0] is not None else 0
        self.io.set_mode(n)
        self.gcolor = 15
        self.lastxy = (0, 0)

    def _pt(self, p):
        p.expect('(')
        x = self.ev(p.parse())
        p.expect(',')
        y = self.ev(p.parse())
        p.expect(')')
        return x, y

    def st_PSET(self, s, erase=False):
        p = ExprParser(self.tok(s.text))
        x, y = self._pt(p)
        c = 0 if erase else self.gcolor
        if p.at_op(','):
            p.next()
            c = self.toint(self.ev(p.parse()))
        self.lastxy = (x, y)
        self.io.gfx('pset', x, y, c)

    def st_PRESET(self, s): self.st_PSET(s, True)

    def st_LINE(self, s):
        p = ExprParser(self.tok(s.text))
        x1, y1 = self.lastxy
        if p.at_op('('):
            x1, y1 = self._pt(p)
        p.expect('-')
        x2, y2 = self._pt(p)
        c, mode = self.gcolor, ''
        if p.at_op(','):
            p.next()
            if not p.at_op(','):
                c = self.toint(self.ev(p.parse()))
            if p.at_op(','):
                p.next()
                mode = p.next()[1].upper()
        self.lastxy = (x2, y2)
        self.io.gfx('line', x1, y1, x2, y2, c, mode)

    def st_CIRCLE(self, s):
        p = ExprParser(self.tok(s.text))
        x, y = self._pt(p)
        p.expect(',')
        r = self.ev(p.parse())
        c = self.gcolor
        if p.at_op(','):
            p.next()
            if not p.at_op(','):
                c = self.toint(self.ev(p.parse()))
        self.lastxy = (x, y)
        self.io.gfx('circle', x, y, r, c)

    # ---------------------------------------------------------------- print / input
    def cur_col(self, f):
        return self.io.screen.c if f is None else self.files[f].get('col', 0)

    def emit(self, f, text):
        if f is None:
            self.io.out(text)
        else:
            fo = self.files.get(f)
            if not fo or fo['mode'] == 'I':
                raise QBError('Bad file number')
            fo['f'].write(text)
            nl = text.rfind('\n')
            fo['col'] = len(text) - nl - 1 if nl >= 0 else fo.get('col', 0) + len(text)

    def st_PRINT(self, s):
        p = ExprParser(self.tok(s.text))
        f = None
        if p.at_op('#'):
            p.next()
            f = self.toint(self.ev(p.parse()))
            if f not in self.files:
                raise QBError('Bad file number')
            if p.at_op(','):
                p.next()
        trailing = False
        if p.at_kw('USING'):
            p.next()
            fmt = self.ev(p.parse())
            p.expect(';')
            vals = []
            while not p.end():
                vals.append(self.ev(p.parse()))
                trailing = False
                if p.at_op(';', ','):
                    p.next()
                    trailing = True
            self.emit(f, using_format(fmt, vals))
            if not trailing:
                self.emit(f, '\n')
            return
        while not p.end():
            trailing = False
            if p.at_op(';'):
                p.next()
                trailing = True
            elif p.at_op(','):
                p.next()
                col = self.cur_col(f)
                nxt = (col // 14 + 1) * 14
                self.emit(f, '\n' if nxt >= 80 else ' ' * (nxt - col))
                trailing = True
            elif p.at_kw('TAB', 'SPC') and p.peek(1) == ('op', '('):
                which = p.next()[1].upper()
                p.next()
                n = self.toint(self.ev(p.parse()))
                p.expect(')')
                col = self.cur_col(f)
                if which == 'SPC':
                    self.emit(f, ' ' * max(0, n))
                else:
                    if col > n - 1:
                        self.emit(f, '\n')
                        col = 0
                    self.emit(f, ' ' * max(0, n - 1 - col))
            else:
                self.emit(f, fmt_print(self.ev(p.parse())))
        if not trailing:
            self.emit(f, '\n')

    st_LPRINT = st_PRINT
    st_Q = st_PRINT

    def _targets(self, p):
        out = []
        while not p.end():
            out.append(p.p_atom())
            if p.at_op(','):
                p.next()
        return out

    @staticmethod
    def _split_input(line):
        vals = []
        for part in split_q(line, ','):
            t = part.strip()
            if len(t) >= 2 and t.startswith('"') and t.endswith('"'):
                t = t[1:-1]
            vals.append(t)
        return vals

    def _file_values(self, f, count):
        fo = self.files.get(f)
        if not fo or fo['mode'] != 'I':
            raise QBError('Bad file number')
        vals = []
        while len(vals) < count:
            if fo['pos'] >= len(fo['lines']):
                raise QBError('Input past end of file')
            vals.extend(self._split_input(fo['lines'][fo['pos']]))
            fo['pos'] += 1
        return vals[:count]

    def _convert(self, node, txt):
        if self.type_of(node[1]) == '$':
            return txt
        try:
            v = float(txt.strip()) if txt.strip() else 0
        except ValueError:
            raise ValueError
        return int(v) if v == int(v) and '.' not in txt and 'e' not in txt.lower() else v

    def st_INPUT(self, s):
        p = ExprParser(self.tok(s.text))
        f = None
        if p.at_op('#'):
            p.next()
            f = self.toint(self.ev(p.parse()))
            p.expect(',')
            targets = self._targets(p)
            for node, txt in zip(targets, self._file_values(f, len(targets))):
                try:
                    self.assign_node(node, self._convert(node, txt))
                except ValueError:
                    raise QBError('Type mismatch')
            return
        if p.at_op(';'):
            p.next()
        prompt, q = '', True
        if p.peek()[0] == 'str':
            prompt = p.next()[1]
            if p.at_op(';'):
                p.next()
            elif p.at_op(','):
                p.next()
                q = False
            else:
                raise QBError('Syntax error')
        targets = self._targets(p)
        if not targets:
            raise QBError('Syntax error')
        while True:
            line = self.io.input_line(prompt + ('? ' if q else ''))
            vals = self._split_input(line)
            if len(vals) == len(targets):
                try:
                    conv = [self._convert(n, t) for n, t in zip(targets, vals)]
                except ValueError:
                    conv = None
                if conv is not None:
                    for n, v in zip(targets, conv):
                        self.assign_node(n, v)
                    return
            self.io.out('Redo from start\n')
            q = True

    def st_LINE_INPUT(self, s):
        p = ExprParser(self.tok(s.text))
        if p.at_op('#'):
            p.next()
            f = self.toint(self.ev(p.parse()))
            p.expect(',')
            node = p.p_atom()
            fo = self.files.get(f)
            if not fo or fo['mode'] != 'I':
                raise QBError('Bad file number')
            if fo['pos'] >= len(fo['lines']):
                raise QBError('Input past end of file')
            self.assign_node(node, fo['lines'][fo['pos']])
            fo['pos'] += 1
            return
        if p.at_op(';'):
            p.next()
        prompt = ''
        if p.peek()[0] == 'str':
            prompt = p.next()[1]
            if p.at_op(';', ','):
                p.next()
        node = p.p_atom()
        self.assign_node(node, self.io.input_line(prompt))

    # ---------------------------------------------------------------- files
    def st_OPEN(self, s):
        m = re.match(r'^(.+?)\s+FOR\s+(INPUT|OUTPUT|APPEND)\s+AS\s+#?\s*(\S+)', s.text, re.I | re.S)
        if not m:
            raise QBError('Syntax error')
        name = self.ev(self.E(m.group(1)))
        n = self.toint(self.ev(self.E(m.group(3))))
        mode = m.group(2).upper()
        if n in self.files:
            raise QBError('File already open', 55)
        try:
            if mode == 'INPUT':
                with open(name, 'r', encoding='utf-8', errors='replace') as fh:
                    lines = fh.read().split('\n')
                if lines and lines[-1] == '':
                    lines.pop()
                self.files[n] = {'mode': 'I', 'lines': lines, 'pos': 0, 'f': None, 'name': name}
            else:
                self.files[n] = {'mode': 'O', 'f': open(name, 'w' if mode == 'OUTPUT' else 'a', encoding='utf-8'),
                                 'col': 0, 'name': name}
        except FileNotFoundError:
            raise QBError('File not found')
        except OSError:
            raise QBError('Path/File access error', 75)

    def st_CLOSE(self, s):
        t = s.text.strip()
        nums = [self.toint(self.ev(self.E(x.strip().lstrip('#')))) for x in t.split(',') if x.strip()] \
            if t else list(self.files)
        for n in nums:
            fo = self.files.pop(n, None)
            if fo and fo.get('f'):
                fo['f'].close()

    def st_WRITE(self, s):
        p = ExprParser(self.tok(s.text))
        f = None
        if p.at_op('#'):
            p.next()
            f = self.toint(self.ev(p.parse()))
            p.expect(',')
        parts = []
        while not p.end():
            v = self.ev(p.parse())
            parts.append('"%s"' % v if isinstance(v, str) else fmt_num(v))
            if p.at_op(','):
                p.next()
        self.emit(f, ','.join(parts) + '\n')

    # ---------------------------------------------------------------- built-in functions
    def _build_builtins(self):
        io = self.io
        b = {}
        b['ABS'] = abs
        b['SGN'] = lambda x: (x > 0) - (x < 0)
        b['INT'] = lambda x: int(math.floor(x))
        b['FIX'] = lambda x: int(math.trunc(x))
        b['CINT'] = lambda x: int(round(x))
        b['CLNG'] = lambda x: int(round(x))
        b['CSNG'] = float
        b['CDBL'] = lambda x: Dbl(x)
        b['SQR'] = lambda x: math.sqrt(x)
        b['SIN'] = math.sin
        b['COS'] = math.cos
        b['TAN'] = math.tan
        b['ATN'] = math.atan
        b['LOG'] = lambda x: math.log(x)
        b['EXP'] = lambda x: math.exp(x)
        b['RND'] = self._rnd
        b['LEN'] = lambda s: len(self._str(s))
        b['LEFT$'] = lambda s, n: self._str(s)[:int(n)]
        b['RIGHT$'] = lambda s, n: self._str(s)[len(s) - int(n):] if int(n) > 0 else ''
        b['MID$'] = lambda s, st, n=None: self._str(s)[int(st) - 1:(None if n is None else int(st) - 1 + int(n))] if int(st) >= 1 else self._bad()
        b['UCASE$'] = lambda s: self._str(s).upper()
        b['LCASE$'] = lambda s: self._str(s).lower()
        b['LTRIM$'] = lambda s: self._str(s).lstrip(' ')
        b['RTRIM$'] = lambda s: self._str(s).rstrip(' ')
        b['SPACE$'] = lambda n: ' ' * int(n)
        b['STRING$'] = lambda n, c: (c[:1] if isinstance(c, str) else chr(int(c))) * int(n)
        b['STR$'] = fmt_str
        b['VAL'] = lambda s: val_fn(self._str(s))
        b['CHR$'] = lambda n: chr(int(n))
        b['ASC'] = lambda s: ord(self._str(s)[0])
        b['HEX$'] = lambda n: '%X' % int(n)
        b['OCT$'] = lambda n: '%o' % int(n)
        b['INSTR'] = self._instr
        b['TIMER'] = self._timer
        b['DATE$'] = lambda: datetime.date.today().strftime('%m-%d-%Y')
        b['TIME$'] = lambda: datetime.datetime.now().strftime('%H:%M:%S')
        b['INKEY$'] = lambda: io.inkey()
        b['INPUT$'] = lambda n: ''.join(io.wait_key() for _ in range(int(n)))
        b['CSRLIN'] = lambda: io.screen.r + 1
        b['POS'] = lambda x=0: io.screen.c + 1
        b['EOF'] = self._eof
        b['FREEFILE'] = lambda: next(i for i in range(1, 256) if i not in self.files)
        b['FRE'] = lambda x=0: 60000
        b['ERR'] = lambda: self.err
        b['ERL'] = lambda: self.erl
        b['CVI'] = b['CVL'] = lambda s: 0
        self.builtins = b
        self.zerofn = {k: b[k] for k in ('TIMER', 'DATE$', 'TIME$', 'INKEY$', 'CSRLIN', 'RND', 'ERR', 'ERL', 'FREEFILE')}

    @staticmethod
    def _bad():
        raise QBError('Illegal function call')

    @staticmethod
    def _str(s):
        if not isinstance(s, str):
            raise QBError('Type mismatch')
        return s

    def _rnd(self, x=1):
        if x < 0:
            self.rng = random.Random(x)
        elif x == 0:
            return self.last_rnd
        self.last_rnd = self.rng.random()
        return self.last_rnd

    @staticmethod
    def _timer():
        n = datetime.datetime.now()
        return n.hour * 3600 + n.minute * 60 + n.second + n.microsecond / 1e6

    @staticmethod
    def _instr(*a):
        if len(a) == 2:
            return a[0].find(a[1]) + 1
        return a[1].find(a[2], int(a[0]) - 1) + 1

    def _eof(self, n):
        fo = self.files.get(int(n))
        if not fo or fo['mode'] != 'I':
            raise QBError('Bad file number')
        return -1 if fo['pos'] >= len(fo['lines']) else 0


class HeadlessIO:
    """Minimal I/O used for testing the interpreter without any window."""

    def __init__(self, inputs=()):
        self.screen = Screen()
        self.inputs = list(inputs)
        self.stopped = False
        self.live = False
        self.log = []
        self.errors = []

    def out(self, s): self.screen.write(s); self.log.append(s)
    def cls(self): self.screen.clear()
    def error(self, msg, line): self.errors.append(msg); self.out('\n' + msg)
    def input_line(self, prompt):
        self.out(prompt)
        if not self.inputs:
            raise StopProgram()
        v = self.inputs.pop(0)
        self.out(v + '\n')
        return v
    def inkey(self): return ''
    def wait_key(self, t=None): return ' '
    def beep(self): pass
    def sleep(self, s): pass
    def set_mode(self, n): pass
    def gfx(self, *a): self.log.append(('gfx',) + a)
    def trace(self, tags): pass


# =====================================================================
#  FLOWCHART  -  source -> tree -> layout primitives (no GUI needed)
# =====================================================================
class TreeBuilder:
    """Turns the interpreter's linked statement list into a structured tree."""

    def __init__(self, it):
        self.it, self.S = it, it.stmts
        refs = set()
        for s in self.S:
            if s.kw in ('GOTO', 'GOSUB', 'ON', 'ON ERROR', 'RESTORE', 'RESUME') or s.kw == 'IF':
                refs.update(w.upper() for w in re.findall(r'\w+', s.text + ' ' + s.then))
        self.lab_at = {}
        for name, idx in it.labels.items():
            if name in refs:
                self.lab_at.setdefault(idx, []).append(name)

    @staticmethod
    def node(t, text, line=0, **kw):
        d = {'t': t, 'text': text, 'line': line}
        d.update(kw)
        return d

    def charts(self):
        out = [{'title': 'MAIN PROGRAM', 'start': 'START', 'end': 'END', 'start_line': 'START',
                'end_line': 'END', 'nodes': self.seq(0, set())[0]}]
        seen = set()
        for info in sorted(self.it.subs.values(), key=lambda x: x['idx']):
            if info['idx'] in seen:
                continue
            seen.add(info['idx'])
            s = self.S[info['idx']]
            nodes = self.seq(info['idx'] + 1, {'END SUB', 'END FUNCTION'})[0]
            out.append({'title': '%s %s' % (info['kind'], info['name']),
                        'start': '%s %s' % (info['kind'], s.text.strip()[:40]),
                        'end': 'END ' + info['kind'], 'start_line': s.line,
                        'end_line': self.S[info['end']].line, 'nodes': nodes})
        return out

    def seq(self, i, stops):
        out, S = [], self.S
        while i < len(S):
            s, k = S[i], S[i].kw
            if k in stops:
                return out, i
            for lab in self.lab_at.get(i, []):
                out.append(self.node('label', lab + ':', s.line))
            self.lab_at.pop(i, None)
            if k in ('SUB', 'FUNCTION') and s.jump is not None:
                i = s.jump + 1
                continue
            if k == 'IF':
                n, i = self.if_(i)
                out.append(n)
                continue
            if k == 'FOR':
                n, i = self.for_(i)
                out.extend(n)
                continue
            if k == 'WHILE':
                body, j = self.seq(i + 1, {'WEND'})
                out.append(self.node('loop', s.text, s.line, body=body, yes='Yes', no='No'))
                i = j + 1
                continue
            if k == 'DO':
                n, i = self.do_(i)
                out.append(n)
                continue
            if k == 'SELECT CASE':
                n, i = self.select_(i)
                out.append(n)
                continue
            n = self.simple(s)
            if n:
                out.append(n)
            i += 1
        return out, i

    def if_(self, i):
        s = self.S[i]
        if not s.block:
            then, els = (s.then, '')
            j = find_kw(s.then, 'ELSE')
            if j >= 0:
                then, els = s.then[:j], s.then[j + 4:]
            return self.node('if', s.cond, s.line, yes=self.inline(then, s.line),
                             no=self.inline(els, s.line)), i + 1
        yes, j = self.seq(i + 1, {'ELSEIF', 'ELSE', 'END IF'})
        t = self.S[j]
        if t.kw == 'END IF':
            no, nxt = [], j + 1
        elif t.kw == 'ELSE':
            no, e = self.seq(j + 1, {'END IF'})
            nxt = e + 1
        else:
            n2, nxt = self.if_(j)
            no = [n2]
        return self.node('if', s.cond, s.line, yes=yes, no=no), nxt

    def inline(self, text, line):
        out = []
        text = text.strip()
        if not text:
            return out
        if re.fullmatch(r'\d+', text):
            return [self.node('goto', 'GOTO ' + text, line)]
        for part in split_q(text, ':'):
            if part.strip():
                try:
                    n = self.simple(self.it.make_stmt(part))
                except QBError:
                    n = self.node('proc', part.strip(), line)
                if n:
                    n['line'] = line
                    out.append(n)
        return out

    def for_(self, i):
        s = self.S[i]
        m = FOR_RE.match(s.text)
        body, j = self.seq(i + 1, {'NEXT'})
        if not m:
            return [self.node('loop', s.text, s.line, body=body, yes='Yes', no='No')], j + 1
        var, a, b, c = m.group(1), m.group(2).strip(), m.group(3).strip(), (m.group(4) or '1').strip()
        neg = c.startswith('-')
        op = '>=' if neg else '<='
        inc = '%s = %s %s %s' % (var, var, '-' if neg else '+', c.lstrip('-+'))
        nxt_line = self.S[j].line
        nodes = [self.node('proc', '%s = %s' % (var, a), s.line, tag='I%d' % s.line),
                 self.node('loop', '%s %s %s ?' % (var, op, b), s.line,
                           body=body + [self.node('proc', inc, nxt_line, tag='N%d' % nxt_line)],
                           yes='Yes', no='No')]
        return nodes, j + 1

    def do_(self, i):
        s = self.S[i]
        m = re.match(r'(?i)^\s*(WHILE|UNTIL)\b(.*)$', s.text)
        body, j = self.seq(i + 1, {'LOOP'})
        e = self.S[j]
        m2 = re.match(r'(?i)^\s*(WHILE|UNTIL)\b(.*)$', e.text)
        if m:
            until = m.group(1).upper() == 'UNTIL'
            return self.node('loop', m.group(2).strip() + ' ?', s.line, body=body,
                             yes='No' if until else 'Yes', no='Yes' if until else 'No'), j + 1
        if m2:
            until = m2.group(1).upper() == 'UNTIL'
            return self.node('post', m2.group(2).strip() + ' ?', e.line, body=body,
                             back='No' if until else 'Yes', out='Yes' if until else 'No'), j + 1
        return self.node('loop', 'Repeat forever ?', s.line, body=body, yes='Yes', no='Exit'), j + 1

    def select_(self, i):
        s = self.S[i]
        cases, j = [], i + 1
        while j < len(self.S) and self.S[j].kw in ('CASE', 'CASE ELSE'):
            c = self.S[j]
            body, j2 = self.seq(j + 1, {'CASE', 'CASE ELSE', 'END SELECT'})
            cases.append((('CASE ELSE' if c.kw == 'CASE ELSE' else 'CASE ' + c.text), body, c.line))
            j = j2
        return self.node('select', 'SELECT CASE ' + s.text, s.line, cases=cases), j + 1

    def simple(self, s):
        k, ln = s.kw, s.line
        if k in ('PRINT', 'LPRINT', '?'):
            return self.node('out', 'PRINT ' + s.text, ln)
        if k in ('INPUT', 'LINE INPUT', 'READ'):
            return self.node('in', '%s %s' % (k, s.text), ln)
        if k == 'CALL':
            return self.node('call', 'CALL ' + s.text, ln)
        if k == 'GOSUB':
            return self.node('call', 'GOSUB ' + s.text, ln)
        if k == 'GOTO':
            return self.node('goto', 'GOTO ' + s.text, ln)
        if k in ('END', 'STOP', 'SYSTEM'):
            return self.node('stop', k, ln)
        if k in ('EXIT SUB', 'EXIT FUNCTION', 'EXIT FOR', 'EXIT DO', 'RETURN'):
            return self.node('exit', k, ln)
        if k in ('REM', 'SHARED', 'STATIC', 'COMMON', 'OPTION', 'DECLARE', 'DEF', 'RANDOMIZE') or k in DEFTYPES:
            return None
        if k in ('END IF', 'END SELECT', 'ELSE'):
            return None
        word = re.match(r'\s*([A-Za-z_][\w.]*[$%&!#]?)', s.raw)
        if word and getattr(self.it, 'st_' + k.replace(' ', '_'), None) is None and k not in NOOPS:
            nm = word.group(1).upper()
            if nm in self.it.subs or nm[:-1] in self.it.subs:
                return self.node('call', 'CALL ' + s.raw, ln)
        return self.node('proc', s.raw, ln)


def wrap_lines(text, width):
    out = []
    for part in str(text).split('\n'):
        out.extend(textwrap.wrap(part, width, break_long_words=True) or [''])
    if len(out) > 5:
        out = out[:4] + [out[4][:max(1, width - 3)] + '...']
    return out


class FlowLayout:
    GAP = 32
    DIAMONDS = ('if', 'loop', 'post', 'select')

    def __init__(self, tw=None):
        self.tw = tw or (lambda s: 7 * len(s))
        self.prims = []

    # ---- primitives
    def shape(self, n, cx, cy):
        w, h = n['_w'], n['_h']
        self.prims.append(('shape', n['t'], cx, cy, w, h, n['_lines'], n.get('tag') or n.get('line', 0)))

    def line(self, pts, arrow=False, label=None):
        self.prims.append(('line', pts, arrow, label))

    def arrow(self, pts):
        self.line(pts, True)

    def text(self, x, y, s):
        self.prims.append(('title', x, y, s))

    # ---- sizes
    def size(self, n):
        if '_w' in n:
            return n['_w'], n['_h']
        t = n['t']
        lines = wrap_lines(n['text'], 20 if t in self.DIAMONDS else 26)
        tw = max(self.tw(l) for l in lines)
        if t in self.DIAMONDS:
            w, h = max(140, tw * 1.6 + 36), max(62, 18 * len(lines) + 46)
        elif t in ('start', 'end', 'stop'):
            w, h = max(96, tw + 44), 38
        elif t == 'label':
            w, h = max(60, tw + 24), 34
        else:
            w, h = max(124, tw + (54 if t in ('in', 'out') else 32)), max(40, 18 * len(lines) + 18)
        n['_w'], n['_h'], n['_lines'] = w, h, lines
        return w, h

    def ext_seq(self, nodes):
        L = R = 0
        for n in nodes:
            l, r = self.ext(n)
            L, R = max(L, l), max(R, r)
        return L, R

    def ext(self, n):
        if '_ext' in n:
            return n['_ext']
        t = n['t']
        w, h = self.size(n)
        hw = w / 2
        if t == 'if':
            yl, yr = self.ext_seq(n['yes'])
            nl, nr = self.ext_seq(n['no'])
            a, b = max(hw + 12, yr + 28), max(hw + 12, nl + 28)
            n['_ab'] = (a, b)
            e = (a + yl, b + nr)
        elif t == 'loop':
            bl, br = self.ext_seq(n['body'])
            e = (max(bl, hw) + 36, max(br, hw) + 40)
        elif t == 'post':
            bl, br = self.ext_seq(n['body'])
            e = (max(bl, hw) + 36, max(br, hw) + 10)
        elif t == 'select':
            cols, total = [], 0
            n['_cn'] = []
            for lab, body, ln in n['cases']:
                cn = {'t': 'case', 'text': lab, 'line': ln}
                self.size(cn)
                n['_cn'].append(cn)
                cols.append(self.ext_seq([cn] + body))
            total = sum(l + r for l, r in cols) + 34 * max(0, len(cols) - 1)
            n['_cols'], n['_total'] = cols, total
            e = (max(total / 2, hw) + 8, max(total / 2, hw) + 8)
        else:
            e = (hw, hw)
        n['_ext'] = e
        return e

    # ---- drawing
    def draw_seq(self, nodes, cx, y):
        prev = None
        for n in nodes:
            top = y if prev is None else prev + self.GAP
            if prev is not None:
                self.arrow([(cx, prev), (cx, top)])
            prev = self.draw(n, cx, top)
        return y if prev is None else prev

    def draw(self, n, cx, y):
        t = n['t']
        w, h = self.size(n)
        if t == 'if':
            return self.d_if(n, cx, y)
        if t == 'loop':
            return self.d_loop(n, cx, y)
        if t == 'post':
            return self.d_post(n, cx, y)
        if t == 'select':
            return self.d_select(n, cx, y)
        self.shape(n, cx, y + h / 2)
        return y + h

    def branch(self, nodes, x, y_from, y_top):
        if not nodes:
            return y_from
        self.arrow([(x, y_from), (x, y_top)])
        return self.draw_seq(nodes, x, y_top)

    def d_if(self, n, cx, y):
        w, h = self.size(n)
        hw, cy = w / 2, y + h / 2
        a, b = n['_ab']
        xl, xr = cx - a, cx + b
        self.shape(n, cx, cy)
        self.line([(cx - hw, cy), (xl, cy)], False, 'Yes')
        self.line([(cx + hw, cy), (xr, cy)], False, 'No')
        yb1 = self.branch(n['yes'], xl, cy, cy + self.GAP)
        yb2 = self.branch(n['no'], xr, cy, cy + self.GAP)
        ym = max(yb1, yb2) + self.GAP
        self.line([(xl, yb1), (xl, ym), (cx, ym)])
        self.line([(xr, yb2), (xr, ym), (cx, ym)])
        return ym

    def d_loop(self, n, cx, y):
        w, h = self.size(n)
        hw, cy = w / 2, y + h / 2
        bl, br = self.ext_seq(n['body'])
        xl, xr = cx - (max(bl, hw) + 22), cx + max(br, hw) + 24
        self.shape(n, cx, cy)
        ytop = y + h + self.GAP
        self.arrow([(cx, y + h), (cx, ytop)])
        self.prims.append(('label', cx + 6, y + h + 10, n.get('yes', 'Yes')))
        ybot = self.draw_seq(n['body'], cx, ytop) if n['body'] else y + h
        yb2 = ybot + self.GAP / 2
        self.line([(cx, ybot), (cx, yb2), (xl, yb2), (xl, cy), (cx - hw, cy)], True)
        yend = yb2 + self.GAP / 2
        self.line([(cx + hw, cy), (xr, cy), (xr, yend), (cx, yend)], False, n.get('no', 'No'))
        return yend

    def d_post(self, n, cx, y):
        w, h = self.size(n)
        hw = w / 2
        bl, br = self.ext_seq(n['body'])
        xl = cx - (max(bl, hw) + 22)
        ybot = self.draw_seq(n['body'], cx, y) if n['body'] else y
        yd = ybot + self.GAP
        self.arrow([(cx, ybot), (cx, yd)])
        cy = yd + h / 2
        self.shape(n, cx, cy)
        self.line([(cx - hw, cy), (xl, cy), (xl, y - self.GAP / 2), (cx, y - self.GAP / 2)], True, n.get('back', 'Yes'))
        self.prims.append(('label', cx + 6, yd + h + 10, n.get('out', 'No')))
        return yd + h

    def d_select(self, n, cx, y):
        w, h = self.size(n)
        cy = y + h / 2
        self.shape(n, cx, cy)
        cols, total = n['_cols'], n['_total']
        ybus = y + h + self.GAP / 2
        self.line([(cx, y + h), (cx, ybus)])
        x = cx - total / 2
        centers, bottoms = [], []
        for (l, r), cn, (lab, body, ln) in zip(cols, n['_cn'], n['cases']):
            xc = x + l
            centers.append(xc)
            x += l + r + 34
        if centers:
            self.line([(min(centers + [cx]), ybus), (max(centers + [cx]), ybus)])
        for xc, cn, (lab, body, ln) in zip(centers, n['_cn'], n['cases']):
            ytop = ybus + self.GAP / 2
            self.arrow([(xc, ybus), (xc, ytop)])
            bottoms.append(self.draw_seq([cn] + body, xc, ytop))
        ym = (max(bottoms) if bottoms else ybus) + self.GAP
        for xc, yb in zip(centers, bottoms):
            self.line([(xc, yb), (xc, ym)])
        if centers:
            self.line([(min(centers + [cx]), ym), (max(centers + [cx]), ym)])
        return ym

    def layout(self, charts):
        x = 50
        for ch in charts:
            nodes = ([{'t': 'start', 'text': ch['start'], 'line': ch.get('start_line', 0)}] + ch['nodes']
                     + [{'t': 'end', 'text': ch['end'], 'line': ch.get('end_line', 0)}])
            L, R = self.ext_seq(nodes)
            cx = x + L
            self.text(cx, 18, ch['title'])
            self.draw_seq(nodes, cx, 40)
            x = cx + R + 110
        return self.prims


def prim_bounds(prims):
    xs, ys = [], []
    for p in prims:
        if p[0] == 'shape':
            xs += [p[2] - p[4] / 2, p[2] + p[4] / 2]
            ys += [p[3] - p[5] / 2, p[3] + p[5] / 2]
        elif p[0] == 'line':
            for x, y in p[1]:
                xs.append(x); ys.append(y)
        elif p[0] in ('title', 'label'):
            xs.append(p[1]); ys.append(p[2])
    if not xs:
        return 0, 0, 400, 300
    return min(xs) - 60, min(ys) - 30, max(xs) + 60, max(ys) + 50


def build_flowchart(it, tw=None):
    return FlowLayout(tw).layout(TreeBuilder(it).charts())


# =====================================================================
#  DATA FLOW DIAGRAMS  (Yourdon / DeMarco notation)
# =====================================================================
def edge_point(shape, tx, ty):
    kind, cx, cy, w, h = shape
    dx, dy = tx - cx, ty - cy
    if dx == 0 and dy == 0:
        return cx, cy
    hw, hh = w / 2, h / 2
    if kind == 'process':
        t = 1 / math.sqrt((dx / hw) ** 2 + (dy / hh) ** 2)
    else:
        t = min(hw / abs(dx) if dx else 1e9, hh / abs(dy) if dy else 1e9)
    return cx + dx * t, cy + dy * t


def analyze_program(it):
    """collect inputs / outputs / files / calls per procedure"""
    S = it.stmts
    spans = [(i['idx'], i['end'], i['name']) for i in {id(v): v for v in it.subs.values()}.values()]
    procs = {'MAIN': {'in': [], 'out': [], 'msgs': False, 'fr': {}, 'fw': {}, 'data': False,
                      'calls': [], 'ar': set(), 'aw': set(), 'kind': 'MAIN'}}
    for _, _, nm in spans:
        procs[nm] = {'in': [], 'out': [], 'msgs': False, 'fr': {}, 'fw': {}, 'data': False,
                     'calls': [], 'ar': set(), 'aw': set(),
                     'kind': it.subs[nm]['kind']}
    arrays = set()
    for s in S:
        if s.kw in ('DIM', 'REDIM'):
            arrays.update(m.upper() for m in re.findall(r'([A-Za-z_]\w*[$%&!#]?)\s*\(', s.text))
    fileno = {}

    def owner(i):
        for a, b, nm in spans:
            if a < i < b:
                return nm
        return 'MAIN'

    def expand(s):
        if s.kw == 'IF' and not s.block:
            res = [s]
            for part in split_q(s.then.replace(' ELSE ', ':').replace(' else ', ':'), ':'):
                if part.strip() and not re.fullmatch(r'\s*\d+\s*', part):
                    try:
                        res.append(it.make_stmt(part))
                    except QBError:
                        pass
            return res
        return [s]

    for i, s0 in enumerate(S):
        P = procs[owner(i)]
        for s in expand(s0):
            k, t = s.kw, s.text
            fm = re.match(r'^\s*#\s*(\w+)\s*,?(.*)$', t)
            if k == 'OPEN':
                m = re.match(r'^(.+?)\s+FOR\s+\w+\s+AS\s+#?\s*(\w+)', t, re.I | re.S)
                if m:
                    nm = m.group(1).strip()
                    fileno[m.group(2)] = nm.strip('"') if nm.startswith('"') else 'FILE ' + m.group(2)
            elif k in ('INPUT', 'LINE INPUT'):
                if fm:
                    P['fr'][fileno.get(fm.group(1), 'FILE ' + fm.group(1))] = fm.group(2).strip()
                else:
                    body = re.sub(r'^\s*;?\s*"[^"]*"\s*[;,]?', '', t)
                    P['in'] += [v.strip() for v in split_q(body, ',', True) if v.strip()]
                    for a in arrays:
                        if re.search(r'\b' + re.escape(a) + r'\s*\(', body.upper()):
                            P['aw'].add(a)
            elif k in ('PRINT', 'LPRINT', '?', 'WRITE'):
                if fm:
                    P['fw'][fileno.get(fm.group(1), 'FILE ' + fm.group(1))] = fm.group(2).strip()
                else:
                    body = re.sub(r'(?i)^\s*USING\s+("[^"]*"|\w+\$)\s*;', '', t)
                    for item in re.split(r'[;,]', body):
                        item = item.strip()
                        if not item:
                            continue
                        if item.startswith('"') or re.match(r'(?i)(TAB|SPC)\(', item):
                            P['msgs'] = True
                        else:
                            P['out'].append(item)
            elif k == 'READ':
                P['data'] = True
                P['in_data'] = P.get('in_data', []) + [v.strip() for v in t.split(',')]
            elif k == 'CALL' or k not in it.handler_words:
                m = re.match(r'^\s*(?:CALL\s+)?([A-Za-z_][\w.]*[$%&!#]?)\s*(.*)$', s.raw, re.I | re.S)
                if (m and m.group(1).upper() in it.subs
                        and not re.match(r'^(\([^)]*\))?\s*=', m.group(2).strip())):
                    P['calls'].append((it.subs[m.group(1).upper()]['name'], m.group(2).strip().strip('()')))
            # function calls inside expressions
            for fn in {v['name'] for v in it.subs.values() if v['kind'] == 'FUNCTION'}:
                base = fn.rstrip('$%&!#')
                if k not in ('FUNCTION', 'SUB') and re.search(r'\b' + re.escape(base) + r'[$%&!#]?\s*\(', s.raw, re.I):
                    if not any(c[0] == fn for c in P['calls']) and owner(i) != fn:
                        P['calls'].append((fn, ''))
            for a in ([] if k in ('DIM', 'REDIM', 'ERASE') else arrays):
                if re.search(r'\b' + re.escape(a) + r'\s*\(', s.raw.upper()):
                    if re.match(r'^\s*' + re.escape(a) + r'\s*\([^=]*\)\s*=', s.raw.upper()) or k == 'READ':
                        P['aw'].add(a)
                    else:
                        P['ar'].add(a)
    return procs, arrays, bool(it.data)


def short(items, n=34):
    seen, out = set(), []
    for x in items:
        if x not in seen:
            seen.add(x)
            out.append(x)
    s = ', '.join(out)
    return s if len(s) <= n else s[:n - 3] + '...'


class DFDLayout:
    def __init__(self):
        self.prims, self.shapes = [], {}

    def add(self, key, kind, cx, cy, w, h, text):
        self.shapes[key] = (kind, cx, cy, w, h)
        self.prims.append(('dshape', kind, cx, cy, w, h, text.split('\n')))

    def flow(self, a, b, label, off=0.0):
        sa, sb = self.shapes[a], self.shapes[b]
        ax, ay = sa[1], sa[2]
        bx, by = sb[1], sb[2]
        dx, dy = bx - ax, by - ay
        d = math.hypot(dx, dy) or 1
        ox, oy = -dy / d * off, dx / d * off
        p1 = edge_point(sa, bx + ox, by + oy)
        p2 = edge_point(sb, ax + ox, ay + oy)
        p1 = (p1[0] + ox, p1[1] + oy)
        p2 = (p2[0] + ox, p2[1] + oy)
        self.prims.append(('line', [p1, p2], True, label, 'mid'))

    def flows(self, flows):
        groups = {}
        for a, b, lab in flows:
            groups.setdefault(frozenset((a, b)), []).append((a, b, lab))
        for g in groups.values():
            k = len(g)
            for i, (a, b, lab) in enumerate(g):
                self.flow(a, b, lab, (i - (k - 1) / 2) * 34)


def build_dfd(it, level=0):
    procs, arrays, has_data = analyze_program(it)
    D = DFDLayout()
    flows = []
    any_in = any(p['in'] for p in procs.values())
    any_out = any(p['out'] or p['msgs'] for p in procs.values())
    files = {}
    for p in procs.values():
        for f in p['fr']:
            files.setdefault(f, set()).add('r')
        for f in p['fw']:
            files.setdefault(f, set()).add('w')
    if level == 0:
        D.add('P', 'process', 470, 250, 190, 120, '0\nQBASIC\nPROGRAM')
        if any_in:
            D.add('U', 'entity', 100, 250, 130, 56, 'USER\n(Keyboard)')
            flows.append(('U', 'P', short([v for p in procs.values() for v in p['in']]) or 'input data'))
        if any_out:
            D.add('O', 'entity', 840, 250, 130, 56, 'DISPLAY\n(Screen)')
            outs = [v for p in procs.values() for v in p['out']]
            flows.append(('P', 'O', ('results: ' + short(outs, 26)) if outs else 'messages'))
        for i, (f, modes) in enumerate(sorted(files.items())):
            key = 'F' + str(i)
            D.add(key, 'entity', 300 + i * 340, 470, 170, 56, 'FILE\n' + f[:20])
            if 'w' in modes:
                flows.append(('P', key, 'records'))
            if 'r' in modes:
                flows.append((key, 'P', 'records'))
        D.flows(flows)
        return D.prims
    names = list(procs)
    cols = min(3, max(1, len(names)))
    for i, nm in enumerate(names):
        r, c = divmod(i, cols)
        label = '%d\n%s' % (i + 1, 'MAIN' if nm == 'MAIN' else nm[:14])
        D.add(nm, 'process', 400 + c * 270, 230 + r * 230, 150, 110, label)
    xr = 400 + (cols - 1) * 270 + 250
    ybottom = 230 + ((len(names) - 1) // cols) * 230 + 230
    if any_in:
        D.add('U', 'entity', 90, 230, 130, 56, 'USER\n(Keyboard)')
    if any_out:
        D.add('O', 'entity', xr, 230, 130, 56, 'DISPLAY\n(Screen)')
    stores = []
    if has_data:
        stores.append(('DATA', 'D%d  DATA'))
    for a in sorted(arrays):
        stores.append(('A_' + a, 'D%d  ARRAY ' + a))
    for f in sorted(files):
        stores.append(('F_' + f, 'D%d  FILE ' + f[:14]))
    for i, (key, lab) in enumerate(stores):
        D.add(key, 'store', 300 + i * 250, ybottom, 190, 44, lab % (i + 1))
    for nm, p in procs.items():
        if p['in']:
            flows.append(('U', nm, short(p['in'])))
        if p['out'] or p['msgs']:
            flows.append((nm, 'O', short(p['out']) if p['out'] else 'messages'))
        if p.get('in_data'):
            flows.append(('DATA', nm, short(p['in_data'])))
        for a in p['aw']:
            flows.append((nm, 'A_' + a, a.lower()))
        for a in p['ar']:
            flows.append(('A_' + a, nm, a.lower()))
        for f, v in p['fw'].items():
            flows.append((nm, 'F_' + f, short([v], 20)))
        for f, v in p['fr'].items():
            flows.append(('F_' + f, nm, short([v], 20)))
        for callee, args in p['calls']:
            if callee in procs:
                flows.append((nm, callee, short([args], 22) if args else 'call'))
                if procs[callee]['kind'] == 'FUNCTION' or args:
                    flows.append((callee, nm, 'result'))
    D.flows([f for f in flows if f[0] in D.shapes and f[1] in D.shapes])
    return D.prims


# =====================================================================
#  IMAGE EXPORT  (JPG / PNG through Pillow - no Tk needed)
# =====================================================================
IMG_FILL = {'start': '#CDEFCF', 'end': '#F3C7C7', 'stop': '#F3C7C7', 'proc': '#D6E6FF', 'call': '#E4D8FF',
            'in': '#FFF1B8', 'out': '#FFE0B2', 'if': '#FFD9D9', 'loop': '#FFD9D9', 'post': '#FFD9D9',
            'select': '#FFD9D9', 'goto': '#FFC8C8', 'exit': '#F3C7C7', 'label': '#EEEEEE', 'case': '#D5F2F2'}


def render_image(prims, path, scale=2.0):
    """Draw flowchart / DFD primitives to a JPG or PNG file.  Needs:  pip install pillow"""
    from PIL import Image, ImageDraw, ImageFont
    x0, y0, x1, y1 = prim_bounds(prims)
    W, H = max(80, int((x1 - x0) * scale)), max(80, int((y1 - y0) * scale))
    img = Image.new('RGB', (W, H), 'white')
    d = ImageDraw.Draw(img)

    def load(sz, bold=False):
        names = (['DejaVuSans-Bold.ttf', 'arialbd.ttf', 'Arial Bold.ttf', 'segoeuib.ttf', 'Helvetica.ttc'] if bold else
                 ['DejaVuSans.ttf', 'arial.ttf', 'Arial.ttf', 'segoeui.ttf', 'Helvetica.ttc'])
        for n in names:
            try:
                return ImageFont.truetype(n, sz)
            except Exception:
                pass
        try:
            return ImageFont.load_default(sz)
        except Exception:
            return ImageFont.load_default()

    f, fb, fs = load(int(10 * scale)), load(int(11 * scale), True), load(int(9 * scale), True)
    ow = max(1, int(1.4 * scale))
    ink = '#222222'

    def X(v): return (v - x0) * scale
    def Y(v): return (v - y0) * scale

    def text(x, y, s, font, fill='#111111'):
        try:
            d.multiline_text((x, y), s, font=font, fill=fill, anchor='mm', align='center')
        except Exception:
            d.multiline_text((x - 20, y - 8), s, font=font, fill=fill)

    def arrow(a, b):
        dx, dy = b[0] - a[0], b[1] - a[1]
        n = math.hypot(dx, dy) or 1
        ux, uy = dx / n, dy / n
        s = 10 * scale
        bx, by = b[0] - ux * s, b[1] - uy * s
        d.polygon([b, (bx - uy * s * 0.4, by + ux * s * 0.4), (bx + uy * s * 0.4, by - ux * s * 0.4)], fill=ink)

    for p in prims:
        k = p[0]
        if k == 'line':
            pts = [(X(a), Y(b)) for a, b in p[1]]
            d.line(pts, fill=ink, width=ow, joint='curve')
            if p[2]:
                arrow(pts[-2], pts[-1])
            if p[3]:
                if len(p) > 4:
                    mx, my = (pts[0][0] + pts[-1][0]) / 2, (pts[0][1] + pts[-1][1]) / 2
                    bb = d.textbbox((mx, my), p[3], font=fs, anchor='mm')
                    d.rectangle([bb[0] - 3, bb[1] - 2, bb[2] + 3, bb[3] + 2], fill='white')
                    text(mx, my, p[3], fs, '#222222')
                else:
                    (ax, ay), (bx, by) = p[1][0], p[1][1]
                    n = math.hypot(bx - ax, by - ay) or 1
                    text(X(ax + (bx - ax) / n * 20), Y(ay + (by - ay) / n * 20) - 9 * scale, p[3], fs,
                         '#B00020' if p[3] in ('No', 'Exit') else '#006400')
        elif k == 'label':
            text(X(p[1]), Y(p[2]), p[3], fs, '#B00020' if p[3] in ('No', 'Exit') else '#006400')
        elif k == 'title':
            text(X(p[1]), Y(p[2]), p[3], fb, '#0000AA')
        elif k == 'shape':
            _, t, cx, cy, w, h, lines, ln = p
            l, r, tp, b = X(cx - w / 2), X(cx + w / 2), Y(cy - h / 2), Y(cy + h / 2)
            mx, my = X(cx), Y(cy)
            fill = IMG_FILL.get(t, '#FFFFFF')
            kw = dict(fill=fill, outline=ink)
            if t in ('start', 'end', 'stop'):
                try:
                    d.rounded_rectangle([l, tp, r, b], radius=(b - tp) / 2, width=ow, **kw)
                except Exception:
                    d.rectangle([l, tp, r, b], width=ow, **kw)
            elif t in ('in', 'out'):
                s = 16 * scale
                d.polygon([(l + s, tp), (r, tp), (r - s, b), (l, b)], **kw)
            elif t in ('if', 'loop', 'post', 'select'):
                d.polygon([(mx, tp), (r, my), (mx, b), (l, my)], **kw)
            elif t == 'label':
                d.ellipse([l, tp, r, b], width=ow, **kw)
            elif t == 'case':
                s = 10 * scale
                d.polygon([(l + s, tp), (r - s, tp), (r, my), (r - s, b), (l + s, b), (l, my)], **kw)
            else:
                d.rectangle([l, tp, r, b], width=ow, **kw)
                if t == 'call':
                    d.line([(l + 9 * scale, tp), (l + 9 * scale, b)], fill=ink, width=ow)
                    d.line([(r - 9 * scale, tp), (r - 9 * scale, b)], fill=ink, width=ow)
            if t in ('in', 'out', 'if', 'loop', 'post', 'select', 'case', 'label', 'start', 'end', 'stop'):
                pass
            text(mx, my, '\n'.join(lines), f)
        elif k == 'dshape':
            _, kind, cx, cy, w, h, lines = p
            l, r, tp, b = X(cx - w / 2), X(cx + w / 2), Y(cy - h / 2), Y(cy + h / 2)
            if kind == 'process':
                d.ellipse([l, tp, r, b], fill='#D6E6FF', outline=ink, width=ow)
            elif kind == 'entity':
                d.rectangle([l, tp, r, b], fill='#FFF1B8', outline=ink, width=ow)
            else:
                d.rectangle([l, tp, r, b], fill='#E3F4E3')
                d.line([(r, tp), (l, tp), (l, b), (r, b)], fill=ink, width=ow)
            text(X(cx), Y(cy), '\n'.join(lines), f)
    ext = os.path.splitext(path)[1].lower()
    if ext in ('.jpg', '.jpeg'):
        img.save(path, 'JPEG', quality=95, dpi=(200, 200))
    else:
        img.save(path)
    return path


KEYWORDS = set('''ABS AND AS ASC ATN BEEP CALL CASE CDBL CHR$ CINT CIRCLE CLNG CLOSE CLS COLOR CONST COS CSNG
CSRLIN DATA DATE$ DECLARE DEF DEFDBL DEFINT DEFLNG DEFSNG DEFSTR DIM DO DOUBLE ELSE ELSEIF END EOF EQV ERASE
ERL ERR ERROR EXIT EXP FIX FOR FUNCTION GOSUB GOTO HEX$ IF IMP INKEY$ INPUT INPUT$ INSTR INT INTEGER IS KEY
LBOUND LCASE$ LEFT$ LEN LET LINE LOCATE LONG LOOP LPRINT LTRIM$ MID$ MOD NEXT NOT OCT$ ON OPEN OPTION OR OUTPUT
APPEND POS PRESET PRINT PSET RANDOMIZE READ REDIM REM RESTORE RESUME RETURN RIGHT$ RND RTRIM$ SCREEN SELECT
SGN SHARED SIN SINGLE SLEEP SPACE$ SPC SQR STATIC STEP STOP STR$ STRING STRING$ SUB SWAP SYSTEM TAB TAN THEN
TIME$ TIMER TO TYPE UBOUND UCASE$ UNTIL USING VAL WEND WHILE WRITE XOR FREEFILE'''.split())

EXAMPLES = {
'Hello / INPUT': '''CLS
PRINT "Hello from QBasic!"
INPUT "What is your name"; n$
PRINT "Nice to meet you, "; n$
''',
'Multiplication table (FOR)': '''CLS
INPUT "Table of"; n
FOR i = 1 TO 10
    PRINT n; "x"; i; "="; n * i
NEXT i
''',
'Even or odd (IF / ELSE)': '''INPUT "Enter a number"; n
IF n MOD 2 = 0 THEN
    PRINT n; "is EVEN"
ELSE
    PRINT n; "is ODD"
END IF
''',
'Factorial (FUNCTION)': '''DECLARE FUNCTION Fact& (n%)
INPUT "Number"; n%
PRINT n%; "! ="; Fact&(n%)
END

FUNCTION Fact& (n%)
    IF n% <= 1 THEN
        Fact& = 1
    ELSE
        Fact& = n% * Fact&(n% - 1)
    END IF
END FUNCTION
''',
'Guess the number (DO LOOP)': '''RANDOMIZE TIMER
secret = INT(RND * 100) + 1
tries = 0
DO
    INPUT "Guess (1-100)"; g
    tries = tries + 1
    IF g < secret THEN
        PRINT "Too low"
    ELSEIF g > secret THEN
        PRINT "Too high"
    END IF
LOOP UNTIL g = secret
PRINT "Correct in"; tries; "tries!"
''',
'Grades (SELECT CASE)': '''INPUT "Marks (0-100)"; m
SELECT CASE m
    CASE 90 TO 100
        PRINT "Grade A"
    CASE 75 TO 89
        PRINT "Grade B"
    CASE 50 TO 74
        PRINT "Grade C"
    CASE ELSE
        PRINT "Fail"
END SELECT
''',
'Bubble sort (SUB + array)': '''DECLARE SUB BubbleSort (a(), n%)
DIM nums(5)
DATA 42, 7, 19, 3, 25, 11
FOR i = 0 TO 5: READ nums(i): NEXT i
CALL BubbleSort(nums(), 5)
FOR i = 0 TO 5
    PRINT nums(i);
NEXT i
PRINT
END

SUB BubbleSort (a(), n%)
    FOR i = 0 TO n% - 1
        FOR j = 0 TO n% - 1 - i
            IF a(j) > a(j + 1) THEN SWAP a(j), a(j + 1)
        NEXT j
    NEXT i
END SUB
''',
'Fibonacci (WHILE)': '''a = 0: b = 1: count = 0
WHILE count < 15
    PRINT a;
    c = a + b: a = b: b = c
    count = count + 1
WEND
PRINT
''',
'Average with DATA / READ': '''total = 0
FOR i = 1 TO 5
    READ x
    total = total + x
NEXT i
PRINT "Average ="; total / 5
PRINT USING "Formatted: ###.##"; total / 5
DATA 12, 18, 7, 30, 21
''',
'Graphics (SCREEN 12)': '''SCREEN 12
FOR r = 10 TO 200 STEP 10
    CIRCLE (320, 240), r, r MOD 15 + 1
NEXT r
LINE (50, 50)-(150, 120), 14, BF
LINE (50, 50)-(150, 120), 15, B
''',
}

HELP_TEXT = """QBASIC IDE  -  quick reference
================================================================
KEYS          F5 run   |   F6 stop   |   Esc (output pane) stop
              Ctrl+N/O/S new/open/save   Ctrl+F find   Ctrl+G go to line
              Click a flowchart box  ->  jumps to that line of code

STATEMENTS    PRINT [USING] ; , TAB() SPC()   INPUT   LINE INPUT   LET
              IF/THEN/ELSE (one-line and block, ELSEIF)   SELECT CASE
              FOR/NEXT/STEP   WHILE/WEND   DO/LOOP [WHILE|UNTIL]
              EXIT FOR / EXIT DO / EXIT SUB / EXIT FUNCTION
              GOTO  GOSUB/RETURN  ON..GOTO/GOSUB  line numbers and labels
              SUB / FUNCTION / CALL / DECLARE / SHARED / DEF FN
              DIM [SHARED] (multi-dim, lo TO hi, AS type)  REDIM  ERASE
              CONST  DATA/READ/RESTORE  SWAP  TYPE...END TYPE (scalar)
              DEFINT/DEFSNG/DEFDBL/DEFLNG/DEFSTR  OPTION BASE
              ON ERROR GOTO / RESUME [NEXT]  ERR  ERL  ERROR n
              OPEN FOR INPUT/OUTPUT/APPEND  PRINT# WRITE# INPUT# LINE INPUT# CLOSE
              CLS COLOR LOCATE BEEP SLEEP RANDOMIZE END STOP SYSTEM
              SCREEN n  PSET PRESET LINE (B, BF)  CIRCLE   (graphics)

FUNCTIONS     ABS SGN INT FIX CINT CLNG CSNG CDBL SQR SIN COS TAN ATN LOG EXP RND
              LEN LEFT$ RIGHT$ MID$ UCASE$ LCASE$ LTRIM$ RTRIM$ SPACE$ STRING$
              STR$ VAL CHR$ ASC INSTR HEX$ OCT$ TIMER DATE$ TIME$ INKEY$ INPUT$
              LBOUND UBOUND EOF FREEFILE CSRLIN POS FRE
OPERATORS     ^  - (unary)  * /  \\  MOD  + -  = <> < > <= >=  NOT AND OR XOR EQV IMP

NOT SUPPORTED PAINT, DRAW, PLAY, SOUND (accepted, ignored), PEEK/POKE, random-access
              files (GET/PUT), arrays of TYPE, graphics VIEW/WINDOW.
NOTE          Like real QBasic, RND gives the same sequence each run unless you
              use RANDOMIZE TIMER.

LIVE TRACE    Press F5: the box being executed turns yellow/red, boxes already run get a
              green border, and a caption names the step (INPUT / PROCESS / LOOP /
              OUTPUT / DECISION / CALL).  Use Delay, Pause and Step to slow it down.

EXPORT        'Export JPG...' saves the current chart (flowchart or DFD) as .jpg/.png
              (needs:  pip install pillow)  or as .ps without Pillow.

CHART PANE    Flowchart : START/END ovals, parallelograms = INPUT/PRINT,
              rectangles = processing, diamonds = decisions/loops,
              double-bar boxes = CALL/GOSUB.  Each SUB/FUNCTION gets its own chart.
              DFD 0 : context diagram (User, Screen, Files <-> program).
              DFD 1 : one bubble per MAIN/SUB/FUNCTION, data stores for DATA,
              arrays and files.   Ctrl+wheel zooms, drag to pan.
"""


class GuiIO:
    """Bridge between the interpreter thread and the Tk screen widgets."""

    def __init__(self):
        self.screen = Screen()
        self.stopped = False
        self.keys = collections.deque(maxlen=64)
        self.input_mode, self.in_buf = False, ''
        self.in_q = queue.Queue()
        self.mode, self.mode_changed = 0, False
        self.gops, self.glock = [], threading.Lock()
        self.error_line = None
        self.finished = False
        self.live, self.delay = True, 300
        self.paused, self.step_req = False, False
        self.events = collections.deque(maxlen=3000)

    def trace(self, tags):                         # interpreter thread, before each statement
        self.events.append(tags)
        while self.paused and not self.step_req:
            if self.stopped:
                raise StopProgram()
            time.sleep(0.02)
        self.step_req = False
        end = time.time() + self.delay / 1000.0
        while time.time() < end:
            if self.stopped:
                raise StopProgram()
            time.sleep(0.005)
        if self.stopped:
            raise StopProgram()

    def out(self, s): self.screen.write(s)

    def cls(self):
        self.screen.clear()
        with self.glock:
            self.gops.append(('cls',))

    def error(self, msg, line):
        self.screen.color(12, 0)
        self.screen.write('\n*** %s ***\n' % msg)
        self.screen.color(7, 0)
        self.error_line = line

    def input_line(self, prompt):
        self.screen.write(prompt)
        self.in_buf = ''
        self.in_q = queue.Queue()
        self.input_mode = True
        v = self.in_q.get()
        self.input_mode = False
        if v is None or self.stopped:
            raise StopProgram()
        return v

    def key_press(self, ch):                       # GUI thread
        if self.input_mode:
            if ch == '\r':
                self.screen.write('\n')
                self.in_q.put(self.in_buf)
            elif ch == '\b':
                if self.in_buf:
                    self.in_buf = self.in_buf[:-1]
                    self.screen.write('\b \b')
            elif len(ch) == 1 and ch >= ' ':
                self.in_buf += ch
                self.screen.write(ch)
        else:
            self.keys.append(ch)

    def inkey(self):
        return self.keys.popleft() if self.keys else ''

    def wait_key(self, t=None):
        while not self.keys:
            if self.stopped:
                raise StopProgram()
            time.sleep(0.02)
        return self.keys.popleft()

    def beep(self): self.screen.bell = True

    def sleep(self, sec):
        if not sec:
            self.wait_key()
            return
        end = time.time() + sec
        while time.time() < end and not self.keys:
            if self.stopped:
                raise StopProgram()
            time.sleep(0.01)

    def set_mode(self, n):
        self.mode = n
        self.screen.clear()
        with self.glock:
            self.gops = [('cls',)]
        self.mode_changed = True

    def gfx(self, *op):
        with self.glock:
            self.gops.append(op)

    def stop(self):
        self.stopped = True
        if self.input_mode:
            self.in_q.put(None)


FLOW_FILL = {'start': '#CDEFCF', 'end': '#F3C7C7', 'stop': '#F3C7C7', 'proc': '#D6E6FF', 'call': '#E4D8FF',
             'in': '#FFF1B8', 'out': '#FFE0B2', 'if': '#FFD9D9', 'loop': '#FFD9D9', 'post': '#FFD9D9',
             'select': '#FFD9D9', 'goto': '#FFC8C8', 'exit': '#F3C7C7', 'label': '#EEEEEE', 'case': '#D5F2F2'}


