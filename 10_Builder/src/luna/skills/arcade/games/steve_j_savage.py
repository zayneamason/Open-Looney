"""Steve J Savage vs. ALIENS — Pyxel arcade game.

Bombay Beach Biennale 2026 kiosk build.
256x144 @ 60 fps, 3-button input (SPACE / LEFT / RIGHT).
"""

import json
import math
import random
import time as _time
from pathlib import Path

import pyxel

# ── Constants ────────────────────────────────────────────────
WIDTH, HEIGHT = 256, 144
CX, CY = WIDTH // 2, HEIGHT // 2
FPS = 60
TUNE_PATH = Path(__file__).parent / "tune.json"
TUNE_POLL_FRAMES = 30  # re-read tune.json every 0.5s


def _load_tune() -> dict:
    """Load tuning parameters from tune.json (tolerant of missing/bad file)."""
    try:
        return json.loads(TUNE_PATH.read_text())
    except Exception:
        return {}


# Defaults matching tune.json
_TUNE_DEFAULTS = {
    "spin_speed": 0.025,
    "fire_cooldown_mult": 1.0,
    "bullet_speed_mult": 1.0,
    "alien_speed_mult": 1.0,
    "spawn_rate_mult": 1.0,
    "hp": 3,
    "timer_secs": 120,
    "savage_charge_secs": 40,
    "savage_duration_secs": 10,
    "combo_decay_secs": 2.0,
    "weapon_cycle_secs": 30,
    "screen_shake": True,
}

# Game states
S_TITLE, S_PLAY, S_OVER = 0, 1, 2

# Weapon IDs
W_DUAL, W_LASER, W_SPREAD, W_MINI = 0, 1, 2, 3
WEAPON_NAMES = ["DUAL GUNS", "LASER", "SPREAD", "MINIGUN"]
WEAPON_CYCLE = 30 * FPS  # 30 seconds per weapon

# Alien types
A_DRONE, A_MEDIUM, A_BOSS = 0, 1, 2

# Scoring tiers
BRONZE, SILVER, GOLD = 25_000, 50_000, 100_000


# ── Entities ─────────────────────────────────────────────────
class Bullet:
    __slots__ = ("x", "y", "vx", "vy", "alive", "dmg", "col")

    def __init__(self, x, y, angle, speed=3.5, dmg=1, col=10):
        self.x, self.y = float(x), float(y)
        self.vx = math.cos(angle) * speed
        self.vy = math.sin(angle) * speed
        self.alive = True
        self.dmg = dmg
        self.col = col

    def update(self):
        self.x += self.vx
        self.y += self.vy
        if self.x < -8 or self.x > WIDTH + 8 or self.y < -8 or self.y > HEIGHT + 8:
            self.alive = False


class Alien:
    __slots__ = ("kind", "x", "y", "hp", "spd", "r", "col", "pts", "alive", "flash")

    def __init__(self, kind, x, y):
        self.kind = kind
        self.x, self.y = float(x), float(y)
        self.alive = True
        self.flash = 0
        if kind == A_DRONE:
            self.hp, self.spd, self.r = 1, 1.2, 4
            self.col, self.pts = 11, 100
        elif kind == A_MEDIUM:
            self.hp, self.spd, self.r = 3, 0.7, 7
            self.col, self.pts = 12, 250
        else:
            self.hp, self.spd, self.r = 10, 0.4, 12
            self.col, self.pts = 8, 1000

    def update(self, speed_mult=1.0):
        dx, dy = CX - self.x, CY - self.y
        d = math.hypot(dx, dy)
        if d > 1:
            self.x += (dx / d) * self.spd * speed_mult
            self.y += (dy / d) * self.spd * speed_mult
        if self.flash > 0:
            self.flash -= 1

    def hit(self, dmg):
        self.hp -= dmg
        self.flash = 3
        if self.hp <= 0:
            self.alive = False
            return True
        return False


class Particle:
    __slots__ = ("x", "y", "vx", "vy", "life", "col")

    def __init__(self, x, y, col):
        self.x, self.y = float(x), float(y)
        a = random.uniform(0, math.tau)
        s = random.uniform(0.5, 2.5)
        self.vx, self.vy = math.cos(a) * s, math.sin(a) * s
        self.life = random.randint(8, 20)
        self.col = col

    def update(self):
        self.x += self.vx
        self.y += self.vy
        self.vx *= 0.94
        self.vy *= 0.94
        self.life -= 1
        return self.life > 0


# ── Main Game ────────────────────────────────────────────────
class Game:
    def __init__(self):
        pyxel.init(WIDTH, HEIGHT, title="Steve J Savage vs. ALIENS", fps=FPS)
        self._init_sounds()
        self._stars = [
            (random.randint(0, WIDTH - 1), random.randint(0, HEIGHT - 1), random.choice([1, 5, 6, 12]))
            for _ in range(80)
        ]
        self.reset()
        pyxel.run(self.update, self.draw)

    # ── Sound setup ──────────────────────────────────────────
    def _init_sounds(self):
        # 0: shoot
        pyxel.sounds[0].set(notes="c3c3", tones="pp", volumes="53", effects="nn", speed=5)
        # 1: alien explode
        pyxel.sounds[1].set(notes="f2d2c2", tones="nnn", volumes="753", effects="fff", speed=7)
        # 2: savage mode activate
        pyxel.sounds[2].set(notes="c3e3g3c4", tones="ssss", volumes="7777", effects="nnnn", speed=10)
        # 3: player hit
        pyxel.sounds[3].set(notes="a2g2", tones="nn", volumes="75", effects="ff", speed=6)
        # 4: weapon switch
        pyxel.sounds[4].set(notes="e3g3", tones="pp", volumes="44", effects="nn", speed=8)
        # 5: combo ding
        pyxel.sounds[5].set(notes="c4e4g4", tones="sss", volumes="654", effects="nnn", speed=12)

    # ── Tuning ───────────────────────────────────────────────
    def _reload_tune(self):
        """Hot-reload tune.json into self.T dict."""
        raw = _load_tune()
        self.T = {k: raw.get(k, v) for k, v in _TUNE_DEFAULTS.items()}

    # ── Reset ────────────────────────────────────────────────
    def reset(self):
        self._reload_tune()
        self.state = S_TITLE
        self.score = 0
        self.hp = int(self.T["hp"])
        self.timer = int(self.T["timer_secs"] * FPS)
        self.frame = 0
        self._tune_poll = 0

        # Rotation
        self.angle = -math.pi / 2  # start pointing up
        self.spin_speed = self.T["spin_speed"]
        self.spin_dir = 1

        # Weapons
        self.weapon = W_DUAL
        self.weapon_t = 0
        self.cooldown = 0

        # Savage mode
        self.savage_charge = 0
        self.savage_on = False
        self.savage_t = 0

        # Combo
        self.combo = 0
        self.combo_t = 0
        self.combo_flash = 0

        # Entities
        self.bullets: list[Bullet] = []
        self.aliens: list[Alien] = []
        self.particles: list[Particle] = []

        # Waves
        self.wave = 0
        self.wave_t = 0
        self.spawn_t = 0

        # Screen shake
        self.shake = 0

    # ── Helpers ───────────────────────────────────────────────
    def combo_mult(self):
        if self.combo >= 15:
            return 4
        if self.combo >= 10:
            return 3
        if self.combo >= 5:
            return 2
        return 1

    def _spawn_alien(self):
        side = random.randint(0, 3)
        if side == 0:
            x, y = random.randint(0, WIDTH), -12
        elif side == 1:
            x, y = random.randint(0, WIDTH), HEIGHT + 12
        elif side == 2:
            x, y = -12, random.randint(0, HEIGHT)
        else:
            x, y = WIDTH + 12, random.randint(0, HEIGHT)

        wp = min(self.wave / 10.0, 1.0)
        r = random.random()
        if r < 0.08 * wp and self.wave >= 3:
            kind = A_BOSS
        elif r < 0.25 + 0.2 * wp:
            kind = A_MEDIUM
        else:
            kind = A_DRONE
        self.aliens.append(Alien(kind, x, y))

    def _fire(self):
        if self.cooldown > 0:
            return

        a = self.angle
        sm = 2 if self.savage_on else 1
        bm = self.T["bullet_speed_mult"]
        cm = self.T["fire_cooldown_mult"]

        if self.weapon == W_DUAL:
            n = random.randint(5, 8) * sm
            spread = math.radians(15)
            for _ in range(n):
                ba = a + random.uniform(-spread / 2, spread / 2)
                self.bullets.append(Bullet(CX, CY, ba, 3.5 * bm, 1, 10))
            self.cooldown = int(18 * cm)

        elif self.weapon == W_LASER:
            n = 3 * sm
            for _ in range(n):
                ba = a + random.uniform(-0.02, 0.02)
                self.bullets.append(Bullet(CX, CY, ba, 6.0 * bm, 3, 14))
            self.cooldown = int(12 * cm)

        elif self.weapon == W_SPREAD:
            n = 10 * sm
            spread = math.radians(50)
            for _ in range(n):
                ba = a + random.uniform(-spread / 2, spread / 2)
                self.bullets.append(Bullet(CX, CY, ba, 2.5 * bm, 1, 9))
            self.cooldown = int(24 * cm)

        elif self.weapon == W_MINI:
            n = 2 * sm
            for _ in range(n):
                ba = a + random.uniform(-0.1, 0.1)
                self.bullets.append(Bullet(CX, CY, ba, 4.5 * bm, 1, 10))
            self.cooldown = max(1, int(3 * cm))

        pyxel.play(0, 0)

    # ── Update ───────────────────────────────────────────────
    def update(self):
        if self.state == S_TITLE:
            if pyxel.btnp(pyxel.KEY_SPACE):
                self.reset()
                self.state = S_PLAY
            return

        if self.state == S_OVER:
            if pyxel.btnp(pyxel.KEY_SPACE):
                self.reset()
            return

        # ── Playing ──
        self.frame += 1
        self.timer -= 1

        # Hot-reload tune.json every TUNE_POLL_FRAMES
        self._tune_poll += 1
        if self._tune_poll >= TUNE_POLL_FRAMES:
            self._tune_poll = 0
            self._reload_tune()
            self.spin_speed = self.T["spin_speed"]

        if self.timer <= 0 or self.hp <= 0:
            self.state = S_OVER
            score_path = Path(__file__).parent / "last_score.json"
            score_path.write_text(json.dumps({
                "game_id": "steve_j_savage",
                "score": self.score,
                "waves": self.wave,
                "hp_remaining": self.hp,
                "medal": "gold" if self.score >= GOLD else "silver" if self.score >= SILVER else "bronze",
                "timestamp": _time.time(),
            }))
            return

        # Rotation
        self.angle += self.spin_speed * self.spin_dir

        # Input
        if pyxel.btnp(pyxel.KEY_LEFT):
            self.spin_dir *= -1

        if pyxel.btn(pyxel.KEY_SPACE):
            self._fire()

        if pyxel.btnp(pyxel.KEY_RIGHT):
            savage_charge_max = int(self.T["savage_charge_secs"] * FPS)
            if self.savage_charge >= savage_charge_max and not self.savage_on:
                self.savage_on = True
                self.savage_t = int(self.T["savage_duration_secs"] * FPS)
                self.savage_charge = 0
                if self.T["screen_shake"]:
                    self.shake = 12
                pyxel.play(2, 2)

        # Cooldown
        if self.cooldown > 0:
            self.cooldown -= 1

        # Savage mode tick
        if self.savage_on:
            self.savage_t -= 1
            if self.savage_t <= 0:
                self.savage_on = False
        else:
            savage_charge_max = int(self.T["savage_charge_secs"] * FPS)
            self.savage_charge = min(self.savage_charge + 1, savage_charge_max)

        # Weapon cycle
        wc = int(self.T["weapon_cycle_secs"] * FPS)
        self.weapon_t += 1
        if self.weapon_t >= wc:
            self.weapon_t = 0
            self.weapon = (self.weapon + 1) % 4
            pyxel.play(0, 4)

        # Combo decay (tunable)
        if self.combo_t > 0:
            self.combo_t -= 1
        else:
            if self.combo > 0:
                self.combo = 0

        if self.combo_flash > 0:
            self.combo_flash -= 1

        # Wave spawning
        self.wave_t += 1
        wave_dur = max(4 * FPS, 14 * FPS - self.wave * FPS)
        if self.wave_t >= wave_dur:
            self.wave += 1
            self.wave_t = 0

        sr = self.T["spawn_rate_mult"]
        interval = max(4, int((55 - self.wave * 3) / sr))
        self.spawn_t += 1
        if self.spawn_t >= interval:
            self.spawn_t = 0
            count = min(1 + self.wave // 3, 6)
            for _ in range(count):
                self._spawn_alien()

        # Update bullets
        for b in self.bullets:
            b.update()
        self.bullets = [b for b in self.bullets if b.alive]

        # Update aliens
        asm = self.T["alien_speed_mult"]
        for a in self.aliens:
            a.update(asm)
            # Collision with player
            d = math.hypot(a.x - CX, a.y - CY)
            if d < a.r + 7:
                if not self.savage_on:
                    self.hp -= 1
                    if self.T["screen_shake"]:
                        self.shake = 8
                    pyxel.play(1, 3)
                a.alive = False
                for _ in range(10):
                    self.particles.append(Particle(a.x, a.y, 8))

        # Bullet-alien collisions
        for b in self.bullets:
            if not b.alive:
                continue
            for a in self.aliens:
                if not a.alive:
                    continue
                if (b.x - a.x) ** 2 + (b.y - a.y) ** 2 < (a.r + 2) ** 2:
                    b.alive = False
                    if a.hit(b.dmg):
                        mult = self.combo_mult()
                        self.score += a.pts * mult
                        self.combo += 1
                        self.combo_t = int(self.T["combo_decay_secs"] * FPS)
                        if self.combo in (5, 10, 15):
                            self.combo_flash = 30
                            pyxel.play(3, 5)
                        else:
                            pyxel.play(1, 1)
                        for _ in range(14):
                            self.particles.append(Particle(a.x, a.y, a.col))
                    else:
                        for _ in range(3):
                            self.particles.append(Particle(b.x, b.y, 7))
                    break

        self.aliens = [a for a in self.aliens if a.alive]

        # Particles
        self.particles = [p for p in self.particles if p.update()]

        # Screen shake decay
        if self.shake > 0:
            self.shake -= 1

    # ── Draw ─────────────────────────────────────────────────
    def draw(self):
        # Shake offset
        sx = random.randint(-self.shake, self.shake) if self.shake else 0
        sy = random.randint(-self.shake, self.shake) if self.shake else 0

        pyxel.cls(0)

        if self.state == S_TITLE:
            self._draw_title()
            return
        if self.state == S_OVER:
            self._draw_gameover()
            return

        # Stars
        for x, y, c in self._stars:
            pyxel.pset(x + sx, y + sy, c)

        # Neon border glow (purple frame like concept art)
        if self.savage_on and pyxel.frame_count % 4 < 2:
            bc = 8  # red flash
        else:
            bc = 2  # dark purple
        pyxel.rectb(sx, sy, WIDTH, HEIGHT, bc)

        # Particles (behind entities)
        for p in self.particles:
            pyxel.pset(int(p.x) + sx, int(p.y) + sy, p.col)

        # Bullets
        for b in self.bullets:
            bx, by = int(b.x) + sx, int(b.y) + sy
            if self.weapon == W_LASER:
                # Laser bullets draw as short lines
                pyxel.line(bx, by, bx - int(b.vx), by - int(b.vy), b.col)
            else:
                pyxel.pset(bx, by, b.col)

        # Aliens
        for a in self.aliens:
            ax, ay = int(a.x) + sx, int(a.y) + sy
            dc = 7 if a.flash > 0 else a.col
            if a.kind == A_DRONE:
                # Small green eye-alien
                pyxel.circ(ax, ay, a.r, dc)
                pyxel.pset(ax, ay, 7)
                # Tiny tentacles
                for t in range(3):
                    ta = math.tau * t / 3 + pyxel.frame_count * 0.05
                    tx = ax + int(math.cos(ta) * (a.r + 2))
                    ty = ay + int(math.sin(ta) * (a.r + 2))
                    pyxel.pset(tx, ty, dc)
            elif a.kind == A_MEDIUM:
                # Blue octopus-brain
                pyxel.circ(ax, ay, a.r, dc)
                pyxel.circ(ax, ay, a.r - 2, 1)
                pyxel.pset(ax - 2, ay - 1, 7)
                pyxel.pset(ax + 2, ay - 1, 7)
                # Tentacles
                for t in range(5):
                    ta = math.tau * t / 5 + pyxel.frame_count * 0.03
                    tx = ax + int(math.cos(ta) * (a.r + 3))
                    ty = ay + int(math.sin(ta) * (a.r + 3))
                    pyxel.line(ax + int(math.cos(ta) * a.r), ay + int(math.sin(ta) * a.r), tx, ty, dc)
            else:
                # Boss — big red multi-eyed
                pyxel.circ(ax, ay, a.r, dc)
                pyxel.circ(ax, ay, a.r - 3, 2)
                # Multiple eyes
                for e in range(4):
                    ea = math.tau * e / 4 + 0.3
                    ex = ax + int(math.cos(ea) * 5)
                    ey = ay + int(math.sin(ea) * 5)
                    pyxel.circ(ex, ey, 2, 10)
                    pyxel.pset(ex, ey, 0)
                # HP bar
                bw = a.r * 2
                hp_max = 10
                pyxel.rect(ax - a.r, ay - a.r - 4, bw, 2, 5)
                pyxel.rect(ax - a.r, ay - a.r - 4, int(bw * a.hp / hp_max), 2, 8)

        # Steve
        self._draw_steve(sx, sy)

        # Aim indicator
        aim_len = 22
        ax2 = CX + int(math.cos(self.angle) * aim_len) + sx
        ay2 = CY + int(math.sin(self.angle) * aim_len) + sy
        col = 8 if self.savage_on and pyxel.frame_count % 4 < 2 else 10
        pyxel.line(CX + sx, CY + sy, ax2, ay2, col)
        # Crosshair dot
        pyxel.circ(ax2, ay2, 1, col)

        # HUD
        self._draw_hud(sx, sy)

    def _draw_steve(self, sx, sy):
        cx, cy = CX + sx, CY + sy

        if self.savage_on and pyxel.frame_count % 6 < 3:
            body, head_c = 10, 15  # yellow flash
        else:
            body, head_c = 3, 15  # military green

        # Shield aura during savage
        if self.savage_on:
            pyxel.circb(cx, cy, 12, 8 if pyxel.frame_count % 4 < 2 else 9)

        # Body
        pyxel.rect(cx - 3, cy - 2, 7, 7, body)
        # Belt
        pyxel.rect(cx - 3, cy + 2, 7, 1, 4)
        # Head
        pyxel.circ(cx, cy - 5, 3, head_c)
        # Hair
        pyxel.rect(cx - 3, cy - 8, 7, 2, 4)
        # Eyes
        pyxel.pset(cx - 1, cy - 5, 0)
        pyxel.pset(cx + 1, cy - 5, 0)
        # Mouth (grin)
        pyxel.line(cx - 1, cy - 3, cx + 1, cy - 3, 0)
        # Legs
        pyxel.rect(cx - 3, cy + 5, 3, 4, 5)
        pyxel.rect(cx + 1, cy + 5, 3, 4, 5)
        # Boots
        pyxel.rect(cx - 3, cy + 8, 3, 1, 4)
        pyxel.rect(cx + 1, cy + 8, 3, 1, 4)

        # Gun arm
        gx = cx + int(math.cos(self.angle) * 10)
        gy = cy + int(math.sin(self.angle) * 10)
        pyxel.line(cx + 2, cy, gx, gy, 5)
        pyxel.line(cx - 2, cy, gx - 1, gy + 1, 5)
        # Gun barrel tips
        pyxel.pset(gx, gy, 6)
        pyxel.pset(gx - 1, gy + 1, 6)

        # Muzzle flash on fire
        if self.cooldown > self.cooldown * 0.7 if self.cooldown else False:
            pyxel.circ(gx + int(math.cos(self.angle) * 3), gy + int(math.sin(self.angle) * 3), 2, 10)

    def _draw_hud(self, sx, sy):
        # Score (top left)
        pyxel.text(4 + sx, 4 + sy, f"SCORE: {self.score:,}", 7)

        # Combo
        mult = self.combo_mult()
        if mult > 1:
            c = 10 if self.combo_flash > 0 and pyxel.frame_count % 6 < 3 else 9
            pyxel.text(4 + sx, 14 + sy, f"COMBO x{mult}", c)

        # Wave
        pyxel.text(4 + sx, 24 + sy, f"WAVE {self.wave}", 13)

        # Timer (top right)
        secs = max(0, self.timer // FPS)
        mins, secs_r = divmod(secs, 60)
        tc = 8 if mins == 0 and secs_r <= 15 else 7
        pyxel.text(WIDTH - 36 + sx, 4 + sy, f"{mins}:{secs_r:02d}", tc)

        # HP (hearts, top right)
        for i in range(self.hp):
            hx = WIDTH - 12 - i * 10 + sx
            hy = 14 + sy
            # Tiny heart shape
            pyxel.rect(hx, hy, 8, 6, 8)
            pyxel.text(hx + 1, hy + 1, "<3", 15)

        # Weapon (bottom left)
        pyxel.text(4 + sx, HEIGHT - 18 + sy, WEAPON_NAMES[self.weapon], 13)
        # Cycle bar
        wc = int(self.T["weapon_cycle_secs"] * FPS)
        prog = self.weapon_t / wc if wc > 0 else 0
        bw = 44
        pyxel.rect(4 + sx, HEIGHT - 10 + sy, bw, 3, 5)
        pyxel.rect(4 + sx, HEIGHT - 10 + sy, int(bw * prog), 3, 13)

        # Savage mode (bottom center)
        if self.savage_on:
            st = self.savage_t // FPS
            label = f"SAVAGE MODE 0:{st:02d}"
            c = 8 if pyxel.frame_count % 4 < 2 else 10
            pyxel.text(CX - len(label) * 2 + sx, HEIGHT - 18 + sy, label, c)
        else:
            savage_max = int(self.T["savage_charge_secs"] * FPS)
            pct = self.savage_charge / savage_max if savage_max > 0 else 0
            if pct >= 1.0:
                c = 10 if pyxel.frame_count % 20 < 10 else 9
                pyxel.text(CX - 24 + sx, HEIGHT - 18 + sy, "SAVAGE READY!", c)
            else:
                bw = 50
                bx = CX - 25 + sx
                by = HEIGHT - 12 + sy
                pyxel.rect(bx, by, bw, 3, 5)
                pyxel.rect(bx, by, int(bw * pct), 3, 8)

    # ── Title Screen ─────────────────────────────────────────
    def _draw_title(self):
        for x, y, c in self._stars:
            pyxel.pset(x, y, c)

        # Purple border
        pyxel.rectb(0, 0, WIDTH, HEIGHT, 2)
        pyxel.rectb(1, 1, WIDTH - 2, HEIGHT - 2, 13)

        # Title
        pyxel.text(CX - 38, 20, "STEVE J SAVAGE", 10)
        pyxel.text(CX - 22, 32, "vs. ALIENS", 8)

        # Decorative line
        pyxel.line(CX - 40, 44, CX + 40, 44, 2)

        # Controls
        pyxel.text(CX - 44, 56, "SPACE : Fire weapons", 7)
        pyxel.text(CX - 44, 66, "LEFT  : Reverse spin", 7)
        pyxel.text(CX - 44, 76, "RIGHT : Savage Mode", 9)

        # Game info
        pyxel.text(CX - 44, 90, "Survive 120 seconds!", 6)
        pyxel.text(CX - 44, 100, "Weapons cycle every 30s", 6)

        # Start prompt
        if pyxel.frame_count % 40 < 28:
            pyxel.text(CX - 28, 118, "PRESS SPACE", 7)

        # Credit
        pyxel.text(4, HEIGHT - 8, "Bombay Beach Biennale 2026", 5)

    # ── Game Over Screen ─────────────────────────────────────
    def _draw_gameover(self):
        for x, y, c in self._stars:
            pyxel.pset(x, y, c)

        pyxel.rectb(0, 0, WIDTH, HEIGHT, 2)

        if self.hp <= 0:
            pyxel.text(CX - 20, 22, "GAME OVER", 8)
        else:
            pyxel.text(CX - 20, 22, "TIME'S UP!", 10)

        pyxel.text(CX - 32, 40, f"FINAL SCORE: {self.score:,}", 7)

        # Medal
        if self.score >= GOLD:
            medal, mc = ">>> GOLD <<<", 10
        elif self.score >= SILVER:
            medal, mc = ">> SILVER <<", 6
        else:
            medal, mc = "> BRONZE <", 9
        pyxel.text(CX - len(medal) * 2, 54, medal, mc)

        # Stats
        pyxel.text(CX - 28, 70, f"Waves survived: {self.wave}", 7)
        pyxel.text(CX - 28, 80, f"Best combo: x{self.combo_mult()}", 7)

        # Restart
        if pyxel.frame_count % 40 < 28:
            pyxel.text(CX - 28, 108, "PRESS SPACE", 7)

        pyxel.text(4, HEIGHT - 8, "Bombay Beach Biennale 2026", 5)


if __name__ == "__main__":
    Game()
