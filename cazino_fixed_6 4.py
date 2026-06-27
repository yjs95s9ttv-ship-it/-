import asyncio
import math
import random
import sqlite3
import time
from io import BytesIO
from collections import Counter
from datetime import datetime, timedelta

import discord
from discord import app_commands
from discord.ext import commands, tasks

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    Image = None
    ImageDraw = None
    ImageFont = None


class PointsEventView(discord.ui.View):
    """永続ポイント配布ボタン。Guildごとのイベントを参照する。"""

    def __init__(self, casino_cog):
        super().__init__(timeout=None)
        self.cog = casino_cog

    @discord.ui.button(
        label="ポイントを受け取る！",
        style=discord.ButtonStyle.success,
        emoji="🎁",
        custom_id="points_event_claim",
    )
    async def claim(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.guild_id is None:
            await interaction.response.send_message("❌ サーバー内でのみ使用できます。", ephemeral=True)
            return

        guild_id = interaction.guild_id
        user_id = interaction.user.id

        with sqlite3.connect(self.cog.db_path) as conn:
            event = conn.execute(
                """
                SELECT id, amount, ends_at
                FROM point_events
                WHERE guild_id = ? AND active = 1
                ORDER BY id DESC
                LIMIT 1
                """,
                (guild_id,),
            ).fetchone()

            if not event:
                await interaction.response.send_message("❌ 現在開催中の配布イベントはありません。", ephemeral=True)
                return

            event_id, amount, ends_at = event
            if ends_at is not None and time.time() > ends_at:
                await interaction.response.send_message("❌ このイベントは既に終了しています。", ephemeral=True)
                return

            already = conn.execute(
                """
                SELECT 1
                FROM point_event_claims
                WHERE guild_id = ? AND event_id = ? AND user_id = ?
                """,
                (guild_id, event_id, user_id),
            ).fetchone()
            if already:
                await interaction.response.send_message("❌ 既に受け取り済みです。", ephemeral=True)
                return

            self.cog._ensure_user_exists(conn, guild_id, user_id)
            now = datetime.now().strftime("%Y-%m-%d")
            conn.execute(
                """
                UPDATE users
                SET points = points + ?, last_updated = ?
                WHERE guild_id = ? AND user_id = ?
                """,
                (amount, now, guild_id, user_id),
            )
            conn.execute(
                """
                INSERT INTO point_event_claims (guild_id, event_id, user_id)
                VALUES (?, ?, ?)
                """,
                (guild_id, event_id, user_id),
            )
            conn.commit()

        await interaction.response.send_message(f"✅ **{amount}pt** を受け取りました！", ephemeral=True)


class CasinoGameView(discord.ui.View):
    """賭け金の先払いとアクティブゲーム管理を共通化する。"""

    def __init__(self, casino_cog, user_id: int, bet: int, guild_id: int, game_name: str):
        super().__init__(timeout=60.0)
        self.cog = casino_cog
        self.user_id = user_id
        self.bet = bet
        self.guild_id = guild_id
        self.game_name = game_name
        self.message: discord.Message | None = None
        self.finished = False

    def bind_message(self, message: discord.Message):
        self.message = message

    def is_owner(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.user_id

    def disable_all_buttons(self):
        for item in self.children:
            item.disabled = True

    def release_session(self):
        if not self.finished:
            self.finished = True
            self.cog.end_active_game(self.guild_id, self.user_id)

    async def close_with_embed(self, interaction: discord.Interaction | None, embed: discord.Embed, already_edited: bool = False):
        self.disable_all_buttons()
        self.release_session()

        target_message = self.message or getattr(interaction, "message", None)
        if already_edited:
            if target_message:
                await target_message.edit(view=self)
        elif interaction and not interaction.response.is_done():
            await interaction.response.edit_message(embed=embed, view=self)
        elif target_message:
            await target_message.edit(embed=embed, view=self)
        self.stop()

    async def on_timeout(self):
        if self.finished:
            return

        self.disable_all_buttons()
        self.release_session()

        if self.message:
            embed = self.build_timeout_embed()
            try:
                await self.message.edit(embed=embed, view=self)
            except discord.HTTPException:
                pass

        self.stop()

    def build_timeout_embed(self) -> discord.Embed:
        embed = discord.Embed(title=f"⏰ {self.game_name} タイムアウト", color=discord.Color.dark_grey())
        embed.description = (
            f"時間切れでゲームを終了しました。\n"
            f"賭け金 **{self.bet}pt** は失われます。"
        )
        return embed


class BlackjackView(CasinoGameView):
    def __init__(self, casino_cog, user_id: int, bet: int, guild_id: int):
        super().__init__(casino_cog, user_id, bet, guild_id, "ブラックジャック")
        self.deck = self._new_deck()
        random.shuffle(self.deck)
        self.player_hand = [self.draw_card(), self.draw_card()]
        self.dealer_hand = [self.draw_card(), self.draw_card()]

    def _new_deck(self):
        return [2, 3, 4, 5, 6, 7, 8, 9, 10, 11] * 4

    def draw_card(self):
        return self.deck.pop()

    def get_hand_total(self, hand):
        total = sum(hand)
        aces = hand.count(11)
        while total > 21 and aces > 0:
            total -= 10
            aces -= 1
        return total

    def is_blackjack(self, hand):
        return len(hand) == 2 and self.get_hand_total(hand) == 21

    def _card_label(self, card_value: int):
        return "A" if card_value == 11 else str(card_value)

    def make_embed(self, show_all_dealer: bool = False, description: str | None = None):
        p_total = self.get_hand_total(self.player_hand)
        d_total = self.get_hand_total(self.dealer_hand)

        embed = discord.Embed(title="🃏 ブラックジャック", color=discord.Color.blue(), description=description)
        if show_all_dealer:
            d_cards = ", ".join(f"[{self._card_label(c)}]" for c in self.dealer_hand)
            embed.add_field(name=f"🤖 ディーラーの手札 (合計: {d_total})", value=d_cards, inline=False)
        else:
            first = self._card_label(self.dealer_hand[0])
            embed.add_field(name="🤖 ディーラーの手札", value=f"[{first}], [❓]", inline=False)

        p_cards = ", ".join(f"[{self._card_label(c)}]" for c in self.player_hand)
        embed.add_field(name=f"👤 あなたの手札 (合計: {p_total})", value=p_cards, inline=False)
        embed.set_footer(text=f"賭け金: {self.bet}pt | 開始時点で賭け金を預かっています")
        return embed

    def build_timeout_embed(self):
        return self.make_embed(
            show_all_dealer=False,
            description=f"⏰ 時間切れです。ゲームを終了したため **{self.bet}pt** は失われました。",
        )

    async def finish_game(self, interaction: discord.Interaction, description: str, color: discord.Color, show_all_dealer: bool = True):
        embed = self.make_embed(show_all_dealer=show_all_dealer, description=description)
        embed.color = color
        await self.close_with_embed(interaction, embed)

    @discord.ui.button(label="ヒット", style=discord.ButtonStyle.primary, emoji="➕")
    async def hit(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.is_owner(interaction):
            await interaction.response.send_message("❌ 他のユーザーのゲームです。", ephemeral=True)
            return

        self.player_hand.append(self.draw_card())
        if self.get_hand_total(self.player_hand) > 21:
            total_pts = self.cog.get_points(self.guild_id, self.user_id)
            await self.finish_game(
                interaction,
                f"💥 **バスト！**\n{self.bet}pt を失いました。\n現在の所持: **{total_pts}pt**",
                discord.Color.red(),
            )
            return

        await interaction.response.edit_message(embed=self.make_embed(), view=self)

    @discord.ui.button(label="スタンド", style=discord.ButtonStyle.success, emoji="✋")
    async def stand(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.is_owner(interaction):
            await interaction.response.send_message("❌ 他のユーザーのゲームです。", ephemeral=True)
            return

        while self.get_hand_total(self.dealer_hand) < 17:
            self.dealer_hand.append(self.draw_card())

        p_total = self.get_hand_total(self.player_hand)
        d_total = self.get_hand_total(self.dealer_hand)

        if d_total > 21 or p_total > d_total:
            new_total = self.cog.add_points(self.guild_id, self.user_id, self.bet * 2)
            await self.finish_game(
                interaction,
                f"🎉 **あなたの勝ちです！**\n**{self.bet * 2}pt** を払い戻しました。\n現在の所持: **{new_total}pt**",
                discord.Color.green(),
            )
        elif p_total == d_total:
            new_total = self.cog.add_points(self.guild_id, self.user_id, self.bet)
            await self.finish_game(
                interaction,
                f"🤝 **引き分け（プッシュ）です。**\n**{self.bet}pt** を返却しました。\n現在の所持: **{new_total}pt**",
                discord.Color.light_grey(),
            )
        else:
            total_pts = self.cog.get_points(self.guild_id, self.user_id)
            await self.finish_game(
                interaction,
                f"💀 **ディーラーの勝ちです。**\n{self.bet}pt を失いました。\n現在の所持: **{total_pts}pt**",
                discord.Color.red(),
            )


class HighLowVisualRenderer:
    """Pillowが使える場合にハイロー用の疑似動画フレームを生成する。"""

    WIDTH = 960
    HEIGHT = 540

    @classmethod
    def available(cls):
        return Image is not None and ImageDraw is not None and ImageFont is not None

    @classmethod
    def _font(cls, size: int, bold: bool = False):
        if not cls.available():
            return None

        candidates = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
        ]
        for path in candidates:
            try:
                return ImageFont.truetype(path, size=size)
            except OSError:
                continue
        return ImageFont.load_default()

    @classmethod
    def _card_label(cls, value: int):
        return {1: "A", 11: "J", 12: "Q", 13: "K"}.get(value, str(value))

    @classmethod
    def _draw_gradient(cls, draw: ImageDraw.ImageDraw):
        for y in range(cls.HEIGHT):
            ratio = y / max(1, cls.HEIGHT - 1)
            r = int(7 + 10 * ratio)
            g = int(18 + 25 * ratio)
            b = int(38 + 45 * ratio)
            draw.line((0, y, cls.WIDTH, y), fill=(r, g, b))

    @classmethod
    def _draw_scene_background(cls, draw: ImageDraw.ImageDraw, scene: str, accent):
        for i in range(0, cls.WIDTH, 80):
            alpha = 18 if i % 160 == 0 else 10
            draw.line((i, 0, i - 140, cls.HEIGHT), fill=accent + (alpha,), width=3)

        if scene == "ready":
            for y in range(120, cls.HEIGHT, 42):
                draw.line((40, y, cls.WIDTH - 40, y), fill=(255, 255, 255, 14), width=2)
        elif scene in {"shuffling", "revealing"}:
            for offset in range(-120, cls.WIDTH, 120):
                draw.polygon(
                    [
                        (offset, 0),
                        (offset + 110, 0),
                        (offset + 280, cls.HEIGHT),
                        (offset + 170, cls.HEIGHT),
                    ],
                    fill=accent + (24 if scene == "shuffling" else 34,),
                )
            if scene == "revealing":
                for x in range(90, cls.WIDTH, 140):
                    draw.line((x, 40, x + 220, cls.HEIGHT - 40), fill=(255, 255, 255, 28), width=5)
        elif scene == "preview":
            for r in range(120, 420, 48):
                draw.ellipse(
                    (cls.WIDTH // 2 - r, cls.HEIGHT // 2 - r, cls.WIDTH // 2 + r, cls.HEIGHT // 2 + r),
                    outline=accent + (16,),
                    width=4,
                )
        elif scene in {"win", "jackpot", "cashout"}:
            center_x, center_y = cls.WIDTH // 2, cls.HEIGHT // 2
            for angle in range(0, 360, 18):
                length = 310 if scene == "jackpot" else 230
                x2 = center_x + int(length * math.cos(math.radians(angle)))
                y2 = center_y + int(length * math.sin(math.radians(angle)))
                draw.line((center_x, center_y, x2, y2), fill=accent + (26,), width=8 if scene == "jackpot" else 5)
        elif scene == "lose":
            for offset in range(-180, cls.WIDTH, 100):
                draw.line((offset, 60, offset + 260, cls.HEIGHT - 40), fill=accent + (44,), width=8)
                draw.line((offset + 30, 40, offset + 290, cls.HEIGHT - 60), fill=(255, 255, 255, 12), width=3)

    @classmethod
    def _draw_telop(cls, draw: ImageDraw.ImageDraw, main_text: str | None, sub_text: str | None, accent, scene: str):
        if not main_text and not sub_text:
            return

        top = 150 if scene in {"lose", "jackpot"} else 156
        height = 104 if sub_text else 74
        left = 54
        right = cls.WIDTH - 54
        draw.rounded_rectangle((left, top, right, top + height), radius=24, fill=(8, 10, 20, 172), outline=accent + (210,), width=3)

        main_font = cls._font(42, bold=True)
        sub_font = cls._font(20, bold=False)

        if main_text:
            bbox = draw.textbbox((0, 0), main_text, font=main_font)
            width = bbox[2] - bbox[0]
            draw.text(((cls.WIDTH - width) / 2, top + 12), main_text, font=main_font, fill=(255, 255, 255))
        if sub_text:
            bbox = draw.textbbox((0, 0), sub_text, font=sub_font)
            width = bbox[2] - bbox[0]
            draw.text(((cls.WIDTH - width) / 2, top + 62), sub_text, font=sub_font, fill=(220, 228, 255))

    @classmethod
    def _draw_card(cls, draw: ImageDraw.ImageDraw, x: int, y: int, value_text: str, accent, hidden: bool = False):
        card_w = 200
        card_h = 280
        radius = 26
        shadow_offset = 10
        draw.rounded_rectangle((x + shadow_offset, y + shadow_offset, x + card_w + shadow_offset, y + card_h + shadow_offset), radius=radius, fill=(0, 0, 0, 110))
        base_fill = (245, 247, 252) if not hidden else (18, 32, 64)
        draw.rounded_rectangle((x, y, x + card_w, y + card_h), radius=radius, fill=base_fill, outline=(255, 255, 255), width=4)

        if hidden:
            for line in range(12):
                y_pos = y + 22 + line * 20
                color = (52 + line * 6, 110 + line * 4, 190 + line * 2)
                draw.line((x + 22, y_pos, x + card_w - 22, y_pos), fill=color, width=6)
            draw.text((x + 68, y + 108), "?", font=cls._font(88, bold=True), fill=(255, 255, 255))
            return

        big_font = cls._font(96, bold=True)
        small_font = cls._font(34, bold=True)
        suit = "♣"
        draw.text((x + 20, y + 16), value_text, font=small_font, fill=accent)
        draw.text((x + 22, y + 54), suit, font=small_font, fill=accent)
        bbox = draw.textbbox((0, 0), value_text, font=big_font)
        text_w = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]
        draw.text((x + (card_w - text_w) / 2, y + (card_h - text_h) / 2 - 20), value_text, font=big_font, fill=accent)
        draw.text((x + (card_w / 2) - 18, y + 190), suit, font=cls._font(52, bold=True), fill=accent)

    @classmethod
    def render_flip_gif(cls, reveal_card_label: str, total_frames: int = 48) -> BytesIO | None:
        """カードめくりアニメーションGIFを生成して返す。"""
        if not cls.available():
            return None

        GW, GH = 900, 600
        CARD_W, CARD_H = 140, 200

        def _table(draw):
            for i in range(GH):
                g = int(35 + 20 * (i / GH))
                draw.line([(0, i), (GW, i)], fill=(15, g + 10, 20, 255))

        def _card_back(draw, x, y, w, h, alpha=255):
            if w < 4:
                return
            r = min(12, w // 4)
            draw.rounded_rectangle((x + 4, y + 4, x + w + 4, y + h + 4), radius=r, fill=(0, 0, 0, 80))
            draw.rounded_rectangle((x, y, x + w, y + h), radius=r, fill=(28, 28, 110, alpha), outline=(160, 160, 230, alpha), width=2)
            if w > 24:
                draw.rounded_rectangle((x + 8, y + 8, x + w - 8, y + h - 8), radius=min(8, w // 6), fill=(40, 40, 150, alpha), outline=(120, 120, 200, alpha), width=1)
            if w > 30:
                for dy in range(20, h - 20, 18):
                    for dx in range(12, w - 12, 18):
                        cx2, cy2 = x + dx, y + dy
                        pts = [(cx2, cy2 - 5), (cx2 + 4, cy2), (cx2, cy2 + 5), (cx2 - 4, cy2)]
                        draw.polygon(pts, fill=(60, 60, 180, alpha))

        def _card_front(draw, x, y, w, h, label="A", alpha=255):
            if w < 4:
                return
            r = min(12, w // 4)
            draw.rounded_rectangle((x + 4, y + 4, x + w + 4, y + h + 4), radius=r, fill=(0, 0, 0, 80))
            draw.rounded_rectangle((x, y, x + w, y + h), radius=r, fill=(245, 245, 255, alpha), outline=(200, 200, 220, alpha), width=2)
            try:
                font_big = cls._font(72, bold=True)
                font_sm = cls._font(26, bold=True)
            except Exception:
                return
            if w > 40 and font_big and font_sm:
                accent = (180, 20, 20, alpha)
                tmp_draw = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
                bb = tmp_draw.textbbox((0, 0), label, font=font_big)
                tw, th = bb[2] - bb[0], bb[3] - bb[1]
                draw.text((x + (w - tw) // 2, y + (h - th) // 2 - 15), label, font=font_big, fill=accent)
                draw.text((x + 8, y + 6), label, font=font_sm, fill=accent)
                draw.text((x + 8, y + 34), "♥", font=font_sm, fill=accent)

        try:
            font_telop = cls._font(22, bold=True)
        except Exception:
            font_telop = None

        frames = []
        pile_x = GW // 2 - CARD_W // 2
        pile_y = GH // 2 - CARD_H // 2 - 20
        pile_count = 8

        for fi in range(total_frames):
            t = fi / (total_frames - 1)
            fade = 1.0 if t <= 0.80 else max(0.0, 1.0 - (t - 0.80) / 0.20)

            img = Image.new("RGBA", (GW, GH), (0, 0, 0, 255))
            draw = ImageDraw.Draw(img, "RGBA")
            _table(draw)

            if t < 0.20:
                lift = 0
            elif t <= 0.55:
                lift = int(55 * ((t - 0.20) / 0.35))
            else:
                lift = 55

            a = int(255 * fade)
            for i in range(pile_count - 1, -1, -1):
                ox, oy = i, -i
                if i == pile_count - 1 and t >= 0.20:
                    _card_back(draw, pile_x + ox, pile_y + oy - lift, CARD_W, CARD_H, alpha=a)
                else:
                    _card_back(draw, pile_x + ox, pile_y + oy, CARD_W, CARD_H, alpha=a)

            card_cx = pile_x + CARD_W // 2
            card_cy = pile_y - lift + CARD_H // 2

            if 0.48 <= t <= 0.80:
                flip_t = (t - 0.48) / 0.32
                ease = 0.5 - math.cos(flip_t * math.pi) / 2
                if ease < 0.5:
                    scale_x = 1.0 - ease * 2
                    cw = max(int(CARD_W * scale_x), 2)
                    _card_back(draw, card_cx - cw // 2, card_cy - CARD_H // 2, cw, CARD_H, alpha=a)
                else:
                    scale_x = (ease - 0.5) * 2
                    cw = max(int(CARD_W * scale_x), 2)
                    _card_front(draw, card_cx - cw // 2, card_cy - CARD_H // 2, cw, CARD_H, label=reveal_card_label, alpha=a)
            elif t > 0.80:
                _card_front(draw, card_cx - CARD_W // 2, card_cy - CARD_H // 2, CARD_W, CARD_H, label=reveal_card_label, alpha=a)

            if t < 0.20:
                msg = "SHUFFLING..."
            elif t < 0.55:
                msg = "CARD REVEAL"
            elif t < 0.80:
                msg = "OPEN THE CARD"
            else:
                msg = ""

            bar_a = int(160 * fade)
            draw.rectangle((0, GH - 50, GW, GH), fill=(0, 0, 0, bar_a))
            if msg and font_telop:
                tmp = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
                bb = tmp.textbbox((0, 0), msg, font=font_telop)
                tw = bb[2] - bb[0]
                draw.text((GW // 2 - tw // 2, GH - 38), msg, font=font_telop, fill=(200, 220, 255, int(255 * fade)))

            frames.append(img.convert("RGBA"))

        output = BytesIO()
        frames[0].save(
            output,
            format="GIF",
            save_all=True,
            append_images=frames[1:],
            duration=50,
            loop=1,  # 1回だけ再生
            disposal=2,
        )
        output.seek(0)
        return output

    @classmethod
    def render_frame(
        cls,
        current_card: int,
        streak: int,
        bet: int,
        bank_text: str,
        phase_title: str,
        phase_text: str,
        guess: str | None = None,
        reveal_card: int | None = None,
        accent=(115, 87, 255),
        flash=False,
        telop_main: str | None = None,
        telop_sub: str | None = None,
        scene: str = "ready",
    ):
        if not cls.available():
            return None

        image = Image.new("RGBA", (cls.WIDTH, cls.HEIGHT), (0, 0, 0, 255))
        draw = ImageDraw.Draw(image, "RGBA")
        cls._draw_gradient(draw)
        cls._draw_scene_background(draw, scene, accent)

        if flash:
            for inset, alpha in ((0, 22), (18, 32), (36, 48)):
                draw.rounded_rectangle(
                    (42 + inset, 40 + inset, 918 - inset, 500 - inset),
                    radius=34,
                    outline=accent + (alpha,),
                    width=4,
                )
            draw.ellipse((210, 180, 750, 500), fill=accent + (18,))

        draw.rounded_rectangle((30, 28, cls.WIDTH - 30, cls.HEIGHT - 28), radius=30, outline=(255, 255, 255, 50), width=2)
        draw.text((54, 42), "HIGH & LOW", font=cls._font(48, bold=True), fill=(255, 255, 255))
        draw.text((56, 98), phase_title, font=cls._font(24, bold=True), fill=accent)
        draw.text((56, 132), phase_text, font=cls._font(24), fill=(219, 226, 255))
        cls._draw_telop(draw, telop_main, telop_sub, accent, scene)

        chip_fill = accent + (255,)
        draw.rounded_rectangle((710, 42, 900, 102), radius=26, fill=chip_fill)
        draw.text((736, 58), bank_text, font=cls._font(24, bold=True), fill=(255, 255, 255))

        draw.rounded_rectangle((72, 200, 888, 470), radius=34, fill=(7, 20, 44, 188), outline=(255, 255, 255, 26), width=2)
        draw.rounded_rectangle((92, 220, 868, 450), radius=28, fill=(11, 30, 58, 152))
        draw.ellipse((250, 238, 710, 448), fill=(255, 255, 255, 10))

        draw.text((84, 196), "Current", font=cls._font(24, bold=True), fill=(214, 222, 255))
        draw.text((620, 196), "Next", font=cls._font(24, bold=True), fill=(214, 222, 255))

        cls._draw_card(draw, 110, 218, cls._card_label(current_card), accent=(42, 60, 90))
        cls._draw_card(draw, 640, 218, cls._card_label(reveal_card) if reveal_card else "", accent=(42, 60, 90), hidden=reveal_card is None)

        line_color = accent + (255,)
        draw.line((480, 220, 480, 454), fill=line_color, width=6)
        draw.text((456, 188), "K", font=cls._font(24, bold=True), fill=(224, 231, 255))
        draw.text((452, 462), "A", font=cls._font(24, bold=True), fill=(224, 231, 255))

        guess_text = {"high": "HIGH", "low": "LOW"}.get(guess, "WAIT")
        draw.text((396, 330), guess_text, font=cls._font(42, bold=True), fill=accent)

        footer_y = 474
        draw.rounded_rectangle((48, footer_y, 910, 516), radius=18, fill=(255, 255, 255, 18))
        draw.text((72, footer_y + 8), f"Bet {bet} pt", font=cls._font(22, bold=True), fill=(255, 255, 255))
        draw.text((238, footer_y + 8), f"Streak {streak}", font=cls._font(22, bold=True), fill=(255, 255, 255))

        output = BytesIO()
        image.convert("RGB").save(output, format="PNG", optimize=True)
        output.seek(0)
        return output


class HighLowView(CasinoGameView):
    def __init__(self, casino_cog, user_id: int, bet: int, guild_id: int):
        super().__init__(casino_cog, user_id, bet, guild_id, "ハイアンドロー")
        self.streak = 0
        self.current_card = random.randint(1, 13)
        self.last_phase = ("LIVE TABLE", "次のカードを読む準備ができました。", None, None, (115, 87, 255), False)

    def get_card_name(self, num: int):
        if num == 1:
            return "A"
        if num == 11:
            return "J"
        if num == 12:
            return "Q"
        if num == 13:
            return "K"
        return str(num)

    def calculate_total_return(self):
        rates = [1.5, 2.2, 3.5, 5.5, 10.0]
        idx = min(self.streak - 1, len(rates) - 1)
        return int(self.bet * rates[idx])

    def make_embed(self, title: str = "🎲 ハイアンドロー", description: str | None = None):
        embed = discord.Embed(title=title, color=discord.Color.purple())
        embed.description = description or "次のカードが今より **High** か **Low** かを当ててください。"
        embed.add_field(name="🎴 現在のカード", value=f"【 **{self.get_card_name(self.current_card)}** 】", inline=True)
        embed.add_field(name="🔥 現在の連勝数", value=f"**{self.streak} 連勝中**", inline=True)

        if self.streak > 0:
            embed.add_field(
                name="💰 現在の確定可能額",
                value=f"**{self.calculate_total_return()}pt**\n(降りるとこの額を受け取ります)",
                inline=False,
            )
        else:
            embed.add_field(name="💰 賭け金", value=f"**{self.bet}pt**", inline=False)

        embed.set_footer(text="ベットは開始時に預かっています | タイは発生しません")
        return embed

    def _current_bank_text(self):
        return f"Bank {self.cog.get_points(self.guild_id, self.user_id)}pt"

    def _build_frame_file(
        self,
        phase_title: str,
        phase_text: str,
        guess: str | None = None,
        reveal_card: int | None = None,
        accent=(115, 87, 255),
        flash=False,
        telop_main: str | None = None,
        telop_sub: str | None = None,
        scene: str = "ready",
    ):
        self.last_phase = (phase_title, phase_text, guess, reveal_card, accent, flash, telop_main, telop_sub, scene)
        rendered = HighLowVisualRenderer.render_frame(
            current_card=self.current_card,
            streak=self.streak,
            bet=self.bet,
            bank_text=self._current_bank_text(),
            phase_title=phase_title,
            phase_text=phase_text,
            guess=guess,
            reveal_card=reveal_card,
            accent=accent,
            flash=flash,
            telop_main=telop_main,
            telop_sub=telop_sub,
            scene=scene,
        )
        if rendered is None:
            return None
        return discord.File(rendered, filename="highlow_live.png")

    async def render_initial_panel(self):
        if not self.message:
            return
        embed = self.make_embed(
            title="LIVE TABLE | READY",
            description="次のカードが今より High か Low かを読んでください。",
        )
        embed.color = discord.Color.purple()
        await self._edit_live_state(
            message=self.message,
            embed=embed,
            accent=(115, 87, 255),
            flash=False,
            telop_main="MAKE YOUR CALL",
            telop_sub="HIGH か LOW を選んで次の一枚を読め",
            scene="ready",
        )

    async def _edit_live_state(
        self,
        *,
        message: discord.Message,
        embed: discord.Embed,
        guess: str | None = None,
        reveal_card: int | None = None,
        accent=(115, 87, 255),
        flash=False,
        view=None,
        telop_main: str | None = None,
        telop_sub: str | None = None,
        scene: str = "ready",
    ):
        file = self._build_frame_file(
            phase_title=embed.title or "HIGH & LOW",
            phase_text=embed.description or "",
            guess=guess,
            reveal_card=reveal_card,
            accent=accent,
            flash=flash,
            telop_main=telop_main,
            telop_sub=telop_sub,
            scene=scene,
        )
        if file:
            embed.set_image(url="attachment://highlow_live.png")
            await message.edit(embed=embed, view=self if view is None else view, attachments=[file])
        else:
            embed.set_image(url=None)
            await message.edit(embed=embed, view=self if view is None else view)

    def _set_playing_buttons(self, can_collect: bool):
        for idx, item in enumerate(self.children):
            item.disabled = False
            if idx == 2:
                item.disabled = not can_collect

    def _set_all_disabled(self):
        for item in self.children:
            item.disabled = True

    def _draw_next_card(self, guess: str):
        old_card = self.current_card
        rig_chance = random.random()
        force_lose = False

        if self.streak == 0 and rig_chance < 0.10:
            force_lose = True
        elif self.streak == 1 and rig_chance < 0.15:
            force_lose = True
        elif self.streak == 2 and rig_chance < 0.25:
            force_lose = True
        elif self.streak == 3 and rig_chance < 0.35:
            force_lose = True
        elif self.streak == 4 and rig_chance < 0.50:
            force_lose = True

        if force_lose:
            if guess == "high":
                candidates = list(range(1, old_card))
            else:
                candidates = list(range(old_card + 1, 14))
            if not candidates:
                candidates = [c for c in range(1, 14) if c != old_card]
        else:
            candidates = [c for c in range(1, 14) if c != old_card]

        next_card = random.choice(candidates)
        is_correct = (guess == "high" and next_card > old_card) or (guess == "low" and next_card < old_card)
        return old_card, next_card, is_correct

    async def process_choice(self, interaction: discord.Interaction, guess: str):
        if not self.is_owner(interaction):
            await interaction.response.send_message("❌ 他のユーザーのゲームです。", ephemeral=True)
            return

        await interaction.response.defer()
        self._set_all_disabled()

        old_card, next_card, is_correct = self._draw_next_card(guess)
        old_name = self.get_card_name(old_card)
        next_name = self.get_card_name(next_card)

        # カードめくりGIFを生成して送信（約2.4秒アニメ、1回再生）
        gif_buf = HighLowVisualRenderer.render_flip_gif(reveal_card_label=self.get_card_name(next_card))
        if gif_buf:
            flip_embed = self.make_embed(
                title="LIVE TABLE | CARD REVEAL",
                description="カードをめくっています...",
            )
            flip_embed.color = discord.Color.dark_purple()
            flip_embed.set_image(url="attachment://card_flip.gif")
            gif_file = discord.File(gif_buf, filename="card_flip.gif")
            await interaction.message.edit(embed=flip_embed, attachments=[gif_file], view=self)
            await asyncio.sleep(2.6)  # GIF再生時間待機
        else:
            # Pillowなし fallback
            loading = self.make_embed(
                title="LIVE TABLE | SHUFFLING",
                description="ディーラーが次のカードを切っています...",
            )
            loading.color = discord.Color.dark_purple()
            await self._edit_live_state(
                message=interaction.message,
                embed=loading,
                guess=guess,
                accent=(111, 76, 255),
                flash=False,
                telop_main="NO TURNING BACK",
                telop_sub="ディーラーが運命の一枚を選んでいます",
                scene="shuffling",
            )
            await asyncio.sleep(1.1)

        if is_correct:
            self.streak += 1
            self.current_card = next_card

            if self.streak >= 5:
                payout = self.calculate_total_return()
                total_pts = self.cog.add_points(self.guild_id, self.user_id, payout)
                self.cog.record_game_result(self.guild_id, self.user_id, "ハイロー", True, earned=payout)
                embed = self.make_embed(
                    title="✨🏆 HIGH & LOW JACKPOT",
                    description=(
                        f"【{old_name}】の次に【**{next_name}**】が出ました。\n"
                        f"見事 **5連勝** です。\n**{payout}pt** を獲得しました！\n"
                        f"現在の所持: **{total_pts}pt**"
                    ),
                )
                embed.color = discord.Color.from_rgb(255, 215, 0)
                await self._edit_live_state(
                    message=interaction.message,
                    embed=embed,
                    guess=guess,
                    reveal_card=next_card,
                    accent=(255, 215, 0),
                    flash=True,
                    view=self,
                    telop_main="JACKPOT",
                    telop_sub="限界突破。5連勝で完全制覇",
                    scene="jackpot",
                )
                await self.close_with_embed(None, embed, already_edited=True)
                return

            self._set_playing_buttons(can_collect=True)
            embed = self.make_embed(
                title="✅ PERFECT READ",
                description=(
                    f"【{old_name}】の次に【**{next_name}**】が出ました。\n"
                    f"まだ続けるなら次の High / Low を選んでください。"
                ),
            )
            embed.color = discord.Color.green()
            await self._edit_live_state(
                message=interaction.message,
                embed=embed,
                guess=guess,
                reveal_card=next_card,
                accent=(52, 201, 122),
                flash=False,
                telop_main="YOU WIN",
                telop_sub="読み切った。この流れはまだ続く",
                scene="win",
            )
            return

        total_pts = self.cog.get_points(self.guild_id, self.user_id)
        self.cog.record_game_result(self.guild_id, self.user_id, "ハイロー", False, lost=self.bet)
        embed = self.make_embed(
            description=(
                f"【{old_name}】の次に【**{next_name}**】が出ました。\n"
                f"{self.bet}pt を失いました。\n現在の所持: **{total_pts}pt**"
            ),
        )
        embed.color = discord.Color.red()
        await self._edit_live_state(
            message=interaction.message,
            embed=embed,
            guess=guess,
            reveal_card=next_card,
            accent=(255, 82, 82),
            flash=True,
            view=self,
            telop_main="YOU LOSE",
            telop_sub="一瞬の判断ミスでゲームオーバー",
            scene="lose",
        )
        await self.close_with_embed(None, embed, already_edited=True)

    @discord.ui.button(label="High", style=discord.ButtonStyle.primary, emoji="⬆️")
    async def high_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.process_choice(interaction, "high")

    @discord.ui.button(label="Low", style=discord.ButtonStyle.danger, emoji="⬇️")
    async def low_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.process_choice(interaction, "low")

    @discord.ui.button(label="ここで降りる", style=discord.ButtonStyle.success, emoji="💰", disabled=True)
    async def collect_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.is_owner(interaction):
            await interaction.response.send_message("❌ 他のユーザーのゲームです。", ephemeral=True)
            return

        payout = self.calculate_total_return()
        total_pts = self.cog.add_points(self.guild_id, self.user_id, payout)
        self.cog.record_game_result(self.guild_id, self.user_id, "ハイロー", True, earned=payout)
        embed = self.make_embed(
            title="💰 CASH OUT",
            description=f"ゲームを終了し **{payout}pt** を受け取りました。\n現在の所持: **{total_pts}pt**",
        )
        embed.color = discord.Color.green()
        await self._edit_live_state(
            message=interaction.message,
            embed=embed,
            accent=(52, 201, 122),
            reveal_card=None,
            flash=True,
            view=self,
            telop_main="CASH OUT",
            telop_sub="ここで確定。勝ち逃げ成功",
            scene="cashout",
        )
        await self.close_with_embed(interaction, embed, already_edited=True)

    def build_timeout_embed(self):
        return self.make_embed(
            title="⏰ ハイアンドロー タイムアウト",
            description=f"時間切れでゲームを終了しました。\n賭け金 **{self.bet}pt** は失われます。",
        )


HAND_PAYOUT = {
    "ロイヤルストレートフラッシュ": 50,
    "ストレートフラッシュ": 20,
    "フォーカード": 10,
    "フルハウス": 6,
    "フラッシュ": 5,
    "ストレート": 4,
    "スリーカード": 3,
    "ツーペア": 2,
    "ワンペア": 1,
    "ハイカード": 0,
}

SUITS = ["S", "H", "D", "C"]
RANKS = ["A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K"]


def new_poker_deck():
    return [(s, r) for s in SUITS for r in RANKS]


def card_str(card):
    suit_map = {"S": "♠", "H": "♥", "D": "♦", "C": "♣"}
    return f"{suit_map[card[0]]}{card[1]}"


def rank_value(rank: str):
    return RANKS.index(rank)


def evaluate_hand(hand):
    suits = [c[0] for c in hand]
    ranks = [c[1] for c in hand]
    vals = sorted(rank_value(r) for r in ranks)

    is_flush = len(set(suits)) == 1
    is_straight = (vals == list(range(vals[0], vals[0] + 5))) or (vals == [0, 1, 2, 3, 12])
    counts = Counter(vals)
    freq = sorted(counts.values(), reverse=True)

    if is_flush and is_straight:
        if set(ranks) == {"10", "J", "Q", "K", "A"}:
            return "ロイヤルストレートフラッシュ"
        return "ストレートフラッシュ"
    if freq[0] == 4:
        return "フォーカード"
    if freq == [3, 2]:
        return "フルハウス"
    if is_flush:
        return "フラッシュ"
    if is_straight:
        return "ストレート"
    if freq[0] == 3:
        return "スリーカード"
    if freq[:2] == [2, 2]:
        return "ツーペア"
    if freq[0] == 2:
        return "ワンペア"
    return "ハイカード"


# ===== SlotRenderer =====

class SlotRenderer:
    WIDTH = 960
    HEIGHT = 540
    REEL_COUNT = 3
    SYMBOL_H = 130
    SYMBOLS = ["7", "💎", "🔔", "🍒", "🍋"]
    SYMBOL_WEIGHTS = [7, 12, 18, 35, 28]
    REEL_X = [160, 400, 640]
    REEL_Y = 170
    REEL_W = 160
    REEL_VIS_H = 160

    PAYOUTS = {
        ("7", "7", "7"): 50,
        ("💎", "💎", "💎"): 20,
        ("🔔", "🔔", "🔔"): 10,
        ("🍒", "🍒", "🍒"): 5,
        ("🍋", "🍋", "🍋"): 3,
    }

    @classmethod
    def available(cls):
        return Image is not None

    @classmethod
    def _font(cls, size: int, bold: bool = False):
        candidates = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
        ]
        for path in candidates:
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                pass
        return ImageFont.load_default()

    @classmethod
    def _draw_symbol(cls, draw, sym: str, cx: int, cy: int, alpha: int = 255):
        W2, H2 = cls.REEL_W - 20, cls.SYMBOL_H - 10
        x, y = cx - W2 // 2, cy - H2 // 2
        bg = {"7": (60,0,0), "💎": (0,30,80), "🔔": (60,50,0), "🍒": (50,0,20), "🍋": (40,50,0)}.get(sym, (30,30,30))
        draw.rounded_rectangle((x,y,x+W2,y+H2), radius=14, fill=bg+(alpha,), outline=(255,255,255,alpha//2), width=2)
        if sym == "7":
            f = cls._font(72, bold=True)
            bb = draw.textbbox((0,0), "7", font=f)
            tw, th = bb[2]-bb[0], bb[3]-bb[1]
            draw.text((cx-tw//2, cy-th//2-4), "7", font=f, fill=(255,40,40,alpha))
        else:
            label = {"💎":"◆","🔔":"♪","🍒":"❤","🍋":"★"}.get(sym, sym)
            color = {"💎":(80,180,255),"🔔":(255,220,60),"🍒":(255,80,100),"🍋":(180,255,80)}.get(sym,(255,255,255))
            f = cls._font(64, bold=True)
            bb = draw.textbbox((0,0), label, font=f)
            tw, th = bb[2]-bb[0], bb[3]-bb[1]
            draw.text((cx-tw//2, cy-th//2-4), label, font=f, fill=color+(alpha,))
            sub = {"💎":"GEM","🔔":"BELL","🍒":"CHERRY","🍋":"LEMON"}.get(sym,"")
            if sub:
                fsub = cls._font(16, bold=True)
                bbs = draw.textbbox((0,0), sub, font=fsub)
                draw.text((cx-(bbs[2]-bbs[0])//2, y+H2-22), sub, font=fsub, fill=color+(max(0,alpha-60),))

    @classmethod
    def _draw_background(cls, draw):
        for i in range(cls.HEIGHT):
            r = int(10+15*i/cls.HEIGHT)
            g = int(5+10*i/cls.HEIGHT)
            b = int(30+20*i/cls.HEIGHT)
            draw.line([(0,i),(cls.WIDTH,i)], fill=(r,g,b,255))
        ft = cls._font(42, bold=True)
        draw.text((cls.WIDTH//2-130, 28), "SLOT MACHINE", font=ft, fill=(255,220,80,255))
        draw.rounded_rectangle((20,20,cls.WIDTH-20,cls.HEIGHT-20), radius=24, outline=(255,220,80,80), width=2)

    @classmethod
    def _draw_reels_frame(cls, draw, display_strips, stopped, scroll_offsets):
        for ri in range(cls.REEL_COUNT):
            cx = cls.REEL_X[ri] + cls.REEL_W // 2
            rx = cls.REEL_X[ri]
            ry = cls.REEL_Y
            draw.rounded_rectangle(
                (rx-4,ry-4,rx+cls.REEL_W+4,ry+cls.REEL_VIS_H+4), radius=16,
                fill=(0,0,0,200),
                outline=(80,255,120,200) if stopped[ri] else (255,220,80,200), width=3,
            )
            cy_center = ry + cls.REEL_VIS_H // 2
            if stopped[ri]:
                cls._draw_symbol(draw, display_strips[ri][1], cx, cy_center)
            else:
                offset_px = int(scroll_offsets[ri] * cls.SYMBOL_H)
                for slot_i, sym in enumerate(display_strips[ri]):
                    sy = cy_center + (slot_i-1)*cls.SYMBOL_H + offset_px
                    if ry - cls.SYMBOL_H < sy < ry + cls.REEL_VIS_H + cls.SYMBOL_H:
                        dist = abs(sy - cy_center)
                        a = max(0, min(255, int(255-(dist/cls.SYMBOL_H)*320)))
                        cls._draw_symbol(draw, sym, cx, sy, alpha=a)
        cy = cls.REEL_Y + cls.REEL_VIS_H // 2
        draw.line([(cls.REEL_X[0]-10,cy),(cls.REEL_X[-1]+cls.REEL_W+10,cy)], fill=(255,220,80,120), width=2)

    @classmethod
    def render_spin_gif(cls, final_symbols: list[str], stopped_flags: list[bool], total_frames: int = 20) -> BytesIO | None:
        if not cls.available():
            return None
        reel_strips = []
        for ri in range(cls.REEL_COUNT):
            reel_strips.append([random.choice(cls.SYMBOLS), final_symbols[ri], random.choice(cls.SYMBOLS)])
        spin_seqs = [[random.choice(cls.SYMBOLS) for _ in range(total_frames*3)] for _ in range(cls.REEL_COUNT)]
        frames = []
        for fi in range(total_frames):
            t = fi / max(total_frames-1, 1)
            img = Image.new("RGBA", (cls.WIDTH, cls.HEIGHT), (0,0,0,255))
            draw = ImageDraw.Draw(img, "RGBA")
            cls._draw_background(draw)
            scroll_offsets = []
            display_strips = []
            for ri in range(cls.REEL_COUNT):
                if stopped_flags[ri]:
                    scroll_offsets.append(0.0)
                    display_strips.append(reel_strips[ri])
                else:
                    scroll_offsets.append((t*5+ri*0.25) % 1.0)
                    idx = fi*3+ri
                    display_strips.append([
                        spin_seqs[ri][idx % len(spin_seqs[ri])],
                        spin_seqs[ri][(idx+1) % len(spin_seqs[ri])],
                        spin_seqs[ri][(idx+2) % len(spin_seqs[ri])],
                    ])
            cls._draw_reels_frame(draw, display_strips, stopped_flags, scroll_offsets)
            font_st = cls._font(22, bold=True)
            stop_count = sum(stopped_flags)
            msg = "▶ ボタンを押してリールを止めろ！" if stop_count==0 else (f"残り {3-stop_count} リール回転中..." if stop_count<3 else "RESULT!")
            bb = draw.textbbox((0,0), msg, font=font_st)
            draw.text((cls.WIDTH//2-(bb[2]-bb[0])//2, cls.HEIGHT-46), msg, font=font_st, fill=(200,220,255,255))
            frames.append(img.convert("RGBA"))
        output = BytesIO()
        frames[0].save(output, format="GIF", save_all=True, append_images=frames[1:], duration=80, loop=0, disposal=2)
        output.seek(0)
        return output

    @classmethod
    def calc_payout(cls, symbols: list[str], bet: int) -> tuple[int, str]:
        key = tuple(symbols)
        if key in cls.PAYOUTS:
            mult = cls.PAYOUTS[key]
            return bet * mult, f"**{mult}倍！**"
        counts = Counter(symbols)
        if max(counts.values()) >= 2:
            return int(bet * 1.5), "**2つ揃い 1.5倍**"
        return 0, "ハズレ..."


# ===== SlotView =====

class SlotView(CasinoGameView):
    SYMBOLS = SlotRenderer.SYMBOLS
    WEIGHTS = SlotRenderer.SYMBOL_WEIGHTS

    def __init__(self, casino_cog, user_id: int, bet: int, guild_id: int):
        super().__init__(casino_cog, user_id, bet, guild_id, "スロット")
        self.final_symbols: list[str] = random.choices(self.SYMBOLS, weights=self.WEIGHTS, k=3)
        self.stopped: list[bool] = [False, False, False]

    async def render_initial(self):
        if self.message:
            await self._send_gif(self.message)

    async def _send_gif(self, message):
        gif = SlotRenderer.render_spin_gif(final_symbols=self.final_symbols, stopped_flags=self.stopped)
        embed = discord.Embed(title="🎰 スロットマシン", color=discord.Color.gold())
        embed.description = f"賭け金: **{self.bet}pt** | 各ボタンを押してリールを止めろ！"
        self._update_buttons()
        if gif:
            embed.set_image(url="attachment://slot.gif")
            f = discord.File(gif, filename="slot.gif")
            await message.edit(embed=embed, attachments=[f], view=self)
        else:
            await message.edit(embed=embed, view=self)

    def _update_buttons(self):
        for i, item in enumerate(self.children):
            if isinstance(item, discord.ui.Button) and i < 3:
                item.disabled = self.stopped[i]
                item.label = f"🎰 STOP {i+1}" if not self.stopped[i] else "✅ STOPPED"
                item.style = discord.ButtonStyle.secondary if self.stopped[i] else discord.ButtonStyle.primary

    async def _stop_reel(self, interaction: discord.Interaction, index: int):
        if not self.is_owner(interaction):
            await interaction.response.send_message("❌ 他のユーザーのゲームです。", ephemeral=True)
            return
        if self.stopped[index]:
            await interaction.response.defer()
            return
        await interaction.response.defer()
        self.stopped[index] = True
        await self._send_gif(interaction.message)
        if all(self.stopped):
            await asyncio.sleep(1.5)
            await self._show_result(interaction.message)

    async def _show_result(self, message):
        payout, desc = SlotRenderer.calc_payout(self.final_symbols, self.bet)
        sym_display = {"7":"🔴7","💎":"💎","🔔":"🔔","🍒":"🍒","🍋":"🍋"}
        syms_str = "  ".join(sym_display.get(s,s) for s in self.final_symbols)
        win = payout > 0
        self.cog.record_game_result(self.guild_id, self.user_id, "スロット", win, earned=payout if win else 0, lost=self.bet if not win else 0)
        if win:
            total = self.cog.add_points(self.guild_id, self.user_id, payout)
            color = discord.Color.from_rgb(255,215,0) if payout >= self.bet*10 else discord.Color.green()
            embed = discord.Embed(title="🎰 スロット結果", color=color)
            embed.description = f"**[ {syms_str} ]**\n\n{desc}\n**+{payout}pt** 獲得！\n現在の所持: **{total}pt**"
        else:
            embed = discord.Embed(title="🎰 スロット結果", color=discord.Color.dark_red())
            embed.description = f"**[ {syms_str} ]**\n\n{desc}\n賭け金 **{self.bet}pt** を失いました。"
        self.disable_all_buttons()
        self.release_session()
        self.finished = True
        await message.edit(embed=embed, attachments=[], view=self)
        self.stop()

    @discord.ui.button(label="🎰 STOP 1", style=discord.ButtonStyle.primary, row=0)
    async def stop1(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._stop_reel(interaction, 0)

    @discord.ui.button(label="🎰 STOP 2", style=discord.ButtonStyle.primary, row=0)
    async def stop2(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._stop_reel(interaction, 1)

    @discord.ui.button(label="🎰 STOP 3", style=discord.ButtonStyle.primary, row=0)
    async def stop3(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._stop_reel(interaction, 2)


class PokerView(CasinoGameView):
    def __init__(self, casino_cog, user_id: int, bet: int, guild_id: int):
        super().__init__(casino_cog, user_id, bet, guild_id, "ポーカー")
        self.phase = "draw"
        self.held = [False] * 5

        deck = new_poker_deck()
        random.shuffle(deck)
        self.hand = [deck.pop() for _ in range(5)]
        self.deck = deck
        self._update_buttons()

    def _update_buttons(self):
        self.clear_items()
        if self.phase != "draw":
            return

        for i in range(5):
            label = f"{'LOCK' if self.held[i] else 'OPEN'} {card_str(self.hand[i])}"
            btn = discord.ui.Button(
                label=label[:80],
                style=discord.ButtonStyle.success if self.held[i] else discord.ButtonStyle.secondary,
                row=0,
            )
            btn.callback = self._make_hold_callback(i)
            self.add_item(btn)

        deal_btn = discord.ui.Button(label="交換して終了", style=discord.ButtonStyle.primary, row=1)
        deal_btn.callback = self.deal_callback
        self.add_item(deal_btn)

    def _make_hold_callback(self, idx: int):
        async def callback(interaction: discord.Interaction):
            if not self.is_owner(interaction):
                await interaction.response.send_message("❌ 他のユーザーのゲームです。", ephemeral=True)
                return
            self.held[idx] = not self.held[idx]
            self._update_buttons()
            await interaction.response.edit_message(embed=self.make_embed(), view=self)

        return callback

    def make_embed(self, result_title: str | None = None, result_desc: str | None = None):
        hand_str = "  ".join(card_str(c) for c in self.hand)
        held_str = "  ".join("🔒" if h else "・" for h in self.held)

        if result_title:
            embed = discord.Embed(title=result_title, description=result_desc, color=discord.Color.blue())
            embed.add_field(name="最終手札", value=f"`{hand_str}`", inline=False)
        else:
            embed = discord.Embed(title="🃏 ポーカー", color=discord.Color.blue())
            embed.description = "残したいカードを押してから `交換して終了` を押してください。"
            embed.add_field(name="手札", value=f"`{hand_str}`", inline=False)
            embed.add_field(name="キープ状態", value=f"`{held_str}`", inline=False)
        embed.set_footer(text=f"賭け金: {self.bet}pt | ベットは開始時に預かっています")
        return embed

    async def deal_callback(self, interaction: discord.Interaction):
        if not self.is_owner(interaction):
            await interaction.response.send_message("❌ 他のユーザーのゲームです。", ephemeral=True)
            return

        for i in range(5):
            if not self.held[i]:
                self.hand[i] = self.deck.pop()

        self.phase = "result"
        self._update_buttons()

        hand_name = evaluate_hand(self.hand)
        multiplier = HAND_PAYOUT[hand_name]

        if multiplier > 0:
            total_return = self.bet * multiplier
            new_total = self.cog.add_points(self.guild_id, self.user_id, total_return)
            if multiplier >= 20:
                color = discord.Color.from_rgb(255, 215, 0)
                title = f"✨🏆 {hand_name}！"
            elif multiplier >= 5:
                color = discord.Color.magenta()
                title = f"🎉 {hand_name}"
            else:
                color = discord.Color.green()
                title = f"✅ {hand_name}"
            desc = f"**{total_return}pt** を払い戻しました。\n現在の所持: **{new_total}pt**"
        else:
            color = discord.Color.red()
            title = "💀 ハイカード（役なし）"
            desc = f"{self.bet}pt を失いました。\n現在の所持: **{self.cog.get_points(self.guild_id, self.user_id)}pt**"

        embed = self.make_embed(result_title=title, result_desc=desc)
        embed.color = color
        await self.close_with_embed(interaction, embed)

    def build_timeout_embed(self):
        return self.make_embed(
            result_title="⏰ ポーカー タイムアウト",
            result_desc=f"時間切れでゲームを終了しました。\n賭け金 **{self.bet}pt** は失われます。",
        )


# ===== BetModal =====

class BetModal(discord.ui.Modal):
    bet_input = discord.ui.TextInput(
        label="ベット数を入力 (50〜1000)",
        placeholder="例: 100",
        min_length=2,
        max_length=4,
    )

    def __init__(self, game: str, casino_cog):
        super().__init__(title=f"🎮 {game} - ベット数入力")
        self.game = game
        self.cog = casino_cog

    async def on_submit(self, interaction: discord.Interaction):
        try:
            bet = int(self.bet_input.value)
        except ValueError:
            await interaction.response.send_message("❌ 整数で入力してください。", ephemeral=True)
            return

        if bet < 50 or bet > 1000:
            await interaction.response.send_message("❌ ベットは **50〜1000pt** の範囲で入力してください。", ephemeral=True)
            return

        guild_id = interaction.guild_id
        user_id = interaction.user.id

        if not self.cog.begin_active_game(guild_id, user_id):
            await interaction.response.send_message("❌ 進行中のゲームがあります。先にそちらを終了してください。", ephemeral=True)
            return

        ok, current = self.cog.deduct_points_if_possible(guild_id, user_id, bet)
        if not ok:
            self.cog.end_active_game(guild_id, user_id)
            await interaction.response.send_message(f"❌ ポイントが足りません。(所持: {current}pt)", ephemeral=True)
            return

        if self.game == "ハイロー":
            view = HighLowView(self.cog, user_id, bet, guild_id)
            await interaction.response.send_message(embed=view.make_embed(), view=view, ephemeral=True)
            view.bind_message(await interaction.original_response())
            await view.render_initial_panel()

        elif self.game == "スロット":
            view = SlotView(self.cog, user_id, bet, guild_id)
            await interaction.response.send_message("🎰 スロット起動中...", view=view, ephemeral=True)
            view.bind_message(await interaction.original_response())
            await view.render_initial()


# ===== CasinoPanelView =====

class CasinoPanelView(discord.ui.View):
    def __init__(self, casino_cog):
        super().__init__(timeout=None)
        self.cog = casino_cog

    @discord.ui.button(label="🎴 ハイロー", style=discord.ButtonStyle.primary, custom_id="panel_highlow", row=0)
    async def highlow_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(BetModal("ハイロー", self.cog))

    @discord.ui.button(label="🎰 スロット", style=discord.ButtonStyle.primary, custom_id="panel_slot", row=0)
    async def slot_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(BetModal("スロット", self.cog))

    @discord.ui.button(label="🎁 デイリー", style=discord.ButtonStyle.success, custom_id="panel_daily", row=1)
    async def daily_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        success, bonus, streak, next_bonus = self.cog.process_daily(interaction.guild_id, interaction.user.id)
        if not success:
            pts = self.cog.get_points(interaction.guild_id, interaction.user.id)
            await interaction.response.send_message(
                f"❌ 今日は既に受け取り済みです。\n明日また来てね！\n現在の所持: **{pts}pt**",
                ephemeral=True,
            )
            return
        pts = self.cog.get_points(interaction.guild_id, interaction.user.id)
        streak_text = f"🔥 {streak}日連続！" if streak > 1 else "初日ボーナス！"
        await interaction.response.send_message(
            f"🎁 **{bonus}pt** を獲得！\n{streak_text}\n明日受け取ると **{next_bonus}pt**\n現在の所持: **{pts}pt**",
            ephemeral=True,
        )

    @discord.ui.button(label="📊 統計", style=discord.ButtonStyle.secondary, custom_id="panel_stats", row=1)
    async def stats_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = self.cog.get_stats_embed(interaction.guild_id, interaction.user.id, interaction.user.display_name)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="🏆 ランキング", style=discord.ButtonStyle.secondary, custom_id="panel_ranking", row=2)
    async def ranking_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = self.cog.create_ranking_embed(interaction.guild_id)
        await interaction.response.send_message(embed=embed, ephemeral=True)


class Casino(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.db_path = "casino.db"
        self.ranking_messages = []
        self.active_games: set[tuple[int, int]] = set()
        self._init_db()
        self.bot.add_view(PointsEventView(self))
        self.bot.add_view(CasinoPanelView(self))
        self.update_ranking_task.start()
        self.reset_inactive_task.start()

    def cog_unload(self):
        self.update_ranking_task.cancel()
        self.reset_inactive_task.cancel()

    def _table_columns(self, conn: sqlite3.Connection, table_name: str):
        rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
        return [row[1] for row in rows]

    def _migrate_point_events(self, conn: sqlite3.Connection):
        cols = self._table_columns(conn, "point_events")
        if not cols:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS point_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    amount INTEGER NOT NULL,
                    ends_at REAL,
                    active INTEGER DEFAULT 1
                )
                """
            )
            return

        if "guild_id" in cols:
            return

        conn.execute("ALTER TABLE point_events RENAME TO point_events_old")
        conn.execute(
            """
            CREATE TABLE point_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                amount INTEGER NOT NULL,
                ends_at REAL,
                active INTEGER DEFAULT 1
            )
            """
        )
        old_rows = conn.execute(
            "SELECT amount, ends_at, active FROM point_events_old"
        ).fetchall()
        for amount, ends_at, active in old_rows:
            conn.execute(
                "INSERT INTO point_events (guild_id, amount, ends_at, active) VALUES (?, ?, ?, ?)",
                (0, amount, ends_at, active),
            )
        conn.execute("DROP TABLE point_events_old")

    def _migrate_point_event_claims(self, conn: sqlite3.Connection):
        cols = self._table_columns(conn, "point_event_claims")
        if not cols:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS point_event_claims (
                    guild_id INTEGER NOT NULL,
                    event_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    PRIMARY KEY (guild_id, event_id, user_id)
                )
                """
            )
            return

        if {"guild_id", "event_id", "user_id"}.issubset(set(cols)):
            return

        conn.execute("ALTER TABLE point_event_claims RENAME TO point_event_claims_old")
        conn.execute(
            """
            CREATE TABLE point_event_claims (
                guild_id INTEGER NOT NULL,
                event_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                PRIMARY KEY (guild_id, event_id, user_id)
            )
            """
        )
        old_rows = conn.execute("SELECT user_id FROM point_event_claims_old").fetchall()
        for (user_id,) in old_rows:
            conn.execute(
                "INSERT OR IGNORE INTO point_event_claims (guild_id, event_id, user_id) VALUES (?, ?, ?)",
                (0, 0, user_id),
            )
        conn.execute("DROP TABLE point_event_claims_old")

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    points INTEGER DEFAULT 100,
                    last_daily TEXT,
                    last_updated TEXT,
                    PRIMARY KEY (guild_id, user_id)
                )
                """
            )

            cols = self._table_columns(conn, "users")
            if "guild_id" not in cols:
                conn.execute("ALTER TABLE users ADD COLUMN guild_id INTEGER NOT NULL DEFAULT 0")
            if "last_updated" not in cols:
                conn.execute("ALTER TABLE users ADD COLUMN last_updated TEXT")
            if "daily_streak" not in cols:
                conn.execute("ALTER TABLE users ADD COLUMN daily_streak INTEGER DEFAULT 0")

            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS game_stats (
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    game TEXT NOT NULL,
                    wins INTEGER DEFAULT 0,
                    losses INTEGER DEFAULT 0,
                    total_earned INTEGER DEFAULT 0,
                    total_lost INTEGER DEFAULT 0,
                    PRIMARY KEY (guild_id, user_id, game)
                )
                """
            )

            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS panel_messages (
                    guild_id INTEGER NOT NULL,
                    channel_id INTEGER NOT NULL,
                    message_id INTEGER NOT NULL,
                    PRIMARY KEY (guild_id)
                )
                """
            )

            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS casino_channels (
                    guild_id INTEGER NOT NULL,
                    channel_id INTEGER NOT NULL,
                    PRIMARY KEY (guild_id, channel_id)
                )
                """
            )

            self._migrate_point_events(conn)
            self._migrate_point_event_claims(conn)
            conn.commit()

    def _ensure_user_exists(self, conn: sqlite3.Connection, guild_id: int, user_id: int):
        conn.execute(
            """
            INSERT OR IGNORE INTO users (guild_id, user_id, points, last_updated)
            VALUES (?, ?, 100, ?)
            """,
            (guild_id, user_id, datetime.now().strftime("%Y-%m-%d")),
        )

    def begin_active_game(self, guild_id: int, user_id: int):
        key = (guild_id, user_id)
        if key in self.active_games:
            return False
        self.active_games.add(key)
        return True

    def end_active_game(self, guild_id: int, user_id: int):
        self.active_games.discard((guild_id, user_id))

    def get_casino_channels(self, guild_id: int):
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT channel_id FROM casino_channels WHERE guild_id = ?",
                (guild_id,),
            ).fetchall()
        return [row[0] for row in rows]

    def is_casino_channel(self, message: discord.Message):
        if message.guild is None:
            return False
        allowed = self.get_casino_channels(message.guild.id)
        if not allowed:
            return True
        return message.channel.id in allowed

    def get_points(self, guild_id: int, user_id: int):
        with sqlite3.connect(self.db_path) as conn:
            self._ensure_user_exists(conn, guild_id, user_id)
            row = conn.execute(
                "SELECT points FROM users WHERE guild_id = ? AND user_id = ?",
                (guild_id, user_id),
            ).fetchone()
            conn.commit()
        return row[0] if row else 100

    def add_points(self, guild_id: int, user_id: int, amount: int):
        now = datetime.now().strftime("%Y-%m-%d")
        with sqlite3.connect(self.db_path) as conn:
            self._ensure_user_exists(conn, guild_id, user_id)
            conn.execute(
                """
                UPDATE users
                SET points = points + ?, last_updated = ?
                WHERE guild_id = ? AND user_id = ?
                """,
                (amount, now, guild_id, user_id),
            )
            row = conn.execute(
                "SELECT points FROM users WHERE guild_id = ? AND user_id = ?",
                (guild_id, user_id),
            ).fetchone()
            conn.commit()
        return row[0]

    def deduct_points_if_possible(self, guild_id: int, user_id: int, amount: int):
        now = datetime.now().strftime("%Y-%m-%d")
        with sqlite3.connect(self.db_path) as conn:
            self._ensure_user_exists(conn, guild_id, user_id)
            cursor = conn.execute(
                """
                UPDATE users
                SET points = points - ?, last_updated = ?
                WHERE guild_id = ? AND user_id = ? AND points >= ?
                """,
                (amount, now, guild_id, user_id, amount),
            )
            if cursor.rowcount == 0:
                conn.rollback()
                row = conn.execute(
                    "SELECT points FROM users WHERE guild_id = ? AND user_id = ?",
                    (guild_id, user_id),
                ).fetchone()
                return False, row[0] if row else 0

            row = conn.execute(
                "SELECT points FROM users WHERE guild_id = ? AND user_id = ?",
                (guild_id, user_id),
            ).fetchone()
            conn.commit()
        return True, row[0]

    async def start_interactive_game(self, interaction: discord.Interaction, bet: int, view: CasinoGameView):
        if interaction.guild_id is None:
            await interaction.response.send_message("❌ サーバー内でのみ使用できます。", ephemeral=True)
            return False

        if bet <= 0:
            await interaction.response.send_message("❌ 1pt以上を賭けてください。", ephemeral=True)
            return False

        guild_id = interaction.guild_id
        user_id = interaction.user.id

        if not self.begin_active_game(guild_id, user_id):
            await interaction.response.send_message("❌ 進行中のゲームがあります。先にそちらを終了してください。", ephemeral=True)
            return False

        ok, current = self.deduct_points_if_possible(guild_id, user_id, bet)
        if not ok:
            self.end_active_game(guild_id, user_id)
            await interaction.response.send_message(f"❌ ポイントが足りません。(所持: {current}pt)", ephemeral=True)
            return False

        await interaction.response.send_message(embed=view.make_embed(), view=view)
        view.bind_message(await interaction.original_response())
        return True

    @tasks.loop(minutes=10)
    async def update_ranking_task(self):
        for entry in self.ranking_messages[:]:
            message, guild_id = entry
            try:
                await message.edit(embed=self.create_ranking_embed(guild_id))
            except discord.HTTPException:
                self.ranking_messages.remove(entry)

    @tasks.loop(hours=24)
    async def reset_inactive_task(self):
        threshold = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                UPDATE users
                SET points = 100
                WHERE (last_updated IS NULL OR last_updated < ?)
                  AND points != 100
                """,
                (threshold,),
            )
            conn.commit()

    def record_game_result(self, guild_id: int, user_id: int, game: str, win: bool, earned: int = 0, lost: int = 0):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO game_stats (guild_id, user_id, game)
                VALUES (?, ?, ?)
                """,
                (guild_id, user_id, game),
            )
            if win:
                conn.execute(
                    "UPDATE game_stats SET wins = wins + 1, total_earned = total_earned + ? WHERE guild_id = ? AND user_id = ? AND game = ?",
                    (earned, guild_id, user_id, game),
                )
            else:
                conn.execute(
                    "UPDATE game_stats SET losses = losses + 1, total_lost = total_lost + ? WHERE guild_id = ? AND user_id = ? AND game = ?",
                    (lost, guild_id, user_id, game),
                )
            conn.commit()

    def get_stats_embed(self, guild_id: int, user_id: int, username: str) -> discord.Embed:
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT game, wins, losses, total_earned, total_lost FROM game_stats WHERE guild_id = ? AND user_id = ?",
                (guild_id, user_id),
            ).fetchall()
            pts = self.get_points(guild_id, user_id)

        embed = discord.Embed(title=f"📊 {username} の統計", color=discord.Color.blurple())
        embed.add_field(name="💰 現在の所持pt", value=f"**{pts}pt**", inline=False)

        if not rows:
            embed.description = "まだゲームの記録がありません。"
            return embed

        game_labels = {"ハイロー": "🎴 ハイロー", "スロット": "🎰 スロット"}
        for game, wins, losses, earned, lost in rows:
            total = wins + losses
            rate = f"{wins/total*100:.1f}%" if total > 0 else "-%"
            label = game_labels.get(game, game)
            embed.add_field(
                name=label,
                value=f"勝: **{wins}** / 負: **{losses}** | 勝率: **{rate}**\n獲得: +{earned}pt / 損失: -{lost}pt",
                inline=False,
            )
        return embed

    def process_daily(self, guild_id: int, user_id: int) -> tuple[bool, int, int, int]:
        """returns (success, bonus, streak, next_bonus)"""
        now = datetime.now()
        today = now.strftime("%Y-%m-%d")
        yesterday = (now - timedelta(days=1)).strftime("%Y-%m-%d")

        with sqlite3.connect(self.db_path) as conn:
            self._ensure_user_exists(conn, guild_id, user_id)
            row = conn.execute(
                "SELECT last_daily, daily_streak FROM users WHERE guild_id = ? AND user_id = ?",
                (guild_id, user_id),
            ).fetchone()

            last_daily, streak = row if row else (None, 0)
            streak = streak or 0

            if last_daily == today:
                return False, 0, streak, 0

            if last_daily == yesterday:
                streak += 1
            else:
                streak = 1

            bonus = 500 + (streak - 1) * 50
            next_bonus = 500 + streak * 50

            conn.execute(
                "UPDATE users SET points = points + ?, last_daily = ?, daily_streak = ?, last_updated = ? WHERE guild_id = ? AND user_id = ?",
                (bonus, today, streak, today, guild_id, user_id),
            )
            conn.commit()

        return True, bonus, streak, next_bonus

    def create_ranking_embed(self, guild_id: int):
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                """
                SELECT user_id, points
                FROM users
                WHERE guild_id = ?
                ORDER BY points DESC
                LIMIT 10
                """,
                (guild_id,),
            ).fetchall()

        embed = discord.Embed(title="🏆 ポイントランキング TOP10", color=discord.Color.gold())
        now_text = datetime.now().strftime("%H:%M")
        for i, (user_id, points) in enumerate(rows, start=1):
            user = self.bot.get_user(user_id)
            name = user.display_name if user else f"不明({user_id})"
            embed.add_field(name=f"{i}位: {name}", value=f"{points} pt", inline=False)
        embed.set_footer(text=f"最終更新: {now_text} (10分ごと)")
        return embed

    # ===== 管理者コマンド =====

    @app_commands.command(name="setup_casino_panel", description="【管理者用】カジノパネルを設置します")
    @app_commands.default_permissions(administrator=True)
    async def setup_casino_panel(self, interaction: discord.Interaction):
        if interaction.guild_id is None:
            await interaction.response.send_message("❌ サーバー内でのみ使用できます。", ephemeral=True)
            return

        embed = discord.Embed(
            title="🎮 ゲームパネル",
            description=(
                "遊びたいゲームを選んでください\n\n"
                "🎴 **ハイロー**\nカードの高低を予想して連勝倍率を稼ごう\n\n"
                "🎰 **スロット**\n3つのリールを揃えて大当たり\n\n"
                "🎁 **デイリー**\n毎日1回ボーナスポイントを獲得（連続ログインで増加）\n\n"
                "📊 **統計**\n自分のゲーム統計を確認\n\n"
                "🏆 **ランキング**\nポイントランキングTOP10を確認"
            ),
            color=discord.Color.blurple(),
        )

        view = CasinoPanelView(self)
        await interaction.response.send_message("✅ カジノパネルを設置しました。", ephemeral=True)
        msg = await interaction.channel.send(embed=embed, view=view)

        # panel_messagesに保存（再起動後も使えるようにチャンネル・メッセージIDを保持）
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO panel_messages (guild_id, channel_id, message_id) VALUES (?, ?, ?)",
                (interaction.guild_id, interaction.channel_id, msg.id),
            )
            conn.commit()

    @app_commands.command(name="reset_points", description="【管理者用】ポイントリセット")
    @app_commands.default_permissions(administrator=True)
    async def reset_points(self, interaction: discord.Interaction, target: discord.Member | None = None):
        if interaction.guild_id is None:
            await interaction.response.send_message("❌ サーバー内でのみ使用できます。", ephemeral=True)
            return
        guild_id = interaction.guild_id
        with sqlite3.connect(self.db_path) as conn:
            if target:
                self._ensure_user_exists(conn, guild_id, target.id)
                conn.execute("UPDATE users SET points = 0 WHERE guild_id = ? AND user_id = ?", (guild_id, target.id))
                msg = f"✅ {target.mention} を 0pt に戻しました。"
            else:
                conn.execute("UPDATE users SET points = 0 WHERE guild_id = ?", (guild_id,))
                msg = "✅ 全員のポイントを 0pt にしました。"
            conn.commit()
        await interaction.response.send_message(msg)

    async def _close_point_event_later(self, message: discord.Message, guild_id: int, event_id: int, seconds: float):
        await asyncio.sleep(seconds)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("UPDATE point_events SET active = 0 WHERE id = ? AND guild_id = ?", (event_id, guild_id))
            conn.commit()
        try:
            await message.edit(embed=discord.Embed(title="🏁 配布イベント終了", color=discord.Color.red()), view=None)
        except discord.HTTPException:
            pass

    @app_commands.command(name="event_points", description="【管理者用】ポイント配布イベントを開始します")
    @app_commands.default_permissions(administrator=True)
    async def event_points(self, interaction: discord.Interaction, amount: int, minutes: float):
        if interaction.guild_id is None:
            await interaction.response.send_message("❌ サーバー内でのみ使用できます。", ephemeral=True)
            return
        if amount <= 0 or minutes <= 0:
            await interaction.response.send_message("❌ amount と minutes は正の値にしてください。", ephemeral=True)
            return
        guild_id = interaction.guild_id
        ends_at = time.time() + minutes * 60
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("UPDATE point_events SET active = 0 WHERE guild_id = ?", (guild_id,))
            conn.execute(
                "INSERT INTO point_events (guild_id, amount, ends_at, active) VALUES (?, ?, ?, 1)",
                (guild_id, amount, ends_at),
            )
            event_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            conn.commit()
        view = PointsEventView(self)
        embed = discord.Embed(
            title="🎁 ポイント配布開催",
            description=f"ボタンを押すと **{amount}pt** を受け取れます。\n開催時間: **{minutes}分**",
            color=discord.Color.green(),
        )
        await interaction.response.send_message("✅ ポイント配布イベントを開始しました。", ephemeral=True)
        message = await interaction.channel.send(embed=embed, view=view)
        self.bot.loop.create_task(self._close_point_event_later(message, guild_id, event_id, minutes * 60))

    @app_commands.command(name="give_points", description="【管理者用】ポイント付与")
    @app_commands.default_permissions(administrator=True)
    async def give_points(self, interaction: discord.Interaction, amount: int, target: discord.Member | None = None):
        if interaction.guild_id is None:
            await interaction.response.send_message("❌ サーバー内でのみ使用できます。", ephemeral=True)
            return
        guild_id = interaction.guild_id
        if target:
            total = self.add_points(guild_id, target.id, amount)
            await interaction.response.send_message(f"✅ {target.mention} に {amount}pt 付与しました。現在: {total}pt")
            return
        with sqlite3.connect(self.db_path) as conn:
            now = datetime.now().strftime("%Y-%m-%d")
            conn.execute("UPDATE users SET points = points + ?, last_updated = ? WHERE guild_id = ?", (amount, now, guild_id))
            conn.commit()
        await interaction.response.send_message(f"✅ 全員に {amount}pt 付与しました。")

    @app_commands.command(name="set_casino_channel", description="【管理者用】カジノチャンネルを設定します")
    @app_commands.default_permissions(administrator=True)
    async def set_casino_channel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT OR IGNORE INTO casino_channels (guild_id, channel_id) VALUES (?, ?)",
                (interaction.guild.id, channel.id),
            )
            conn.commit()
        await interaction.response.send_message(f"✅ {channel.mention} をカジノチャンネルに設定しました。", ephemeral=True)

    @app_commands.command(name="unset_casino_channel", description="【管理者用】")
    @app_commands.default_permissions(administrator=True)
    async def unset_casino_channel(self, interaction: discord.Interaction, channel: discord.TextChannel | None = None):
        with sqlite3.connect(self.db_path) as conn:
            if channel:
                conn.execute("DELETE FROM casino_channels WHERE guild_id = ? AND channel_id = ?", (interaction.guild.id, channel.id))
                conn.commit()
                remaining = self.get_casino_channels(interaction.guild.id)
                msg = f"✅ {channel.mention} を解除しました。" + (f"（残り: {', '.join(ch.mention for ch in remaining)}）" if remaining else "")
            else:
                conn.execute("DELETE FROM casino_channels WHERE guild_id = ?", (interaction.guild.id,))
                conn.commit()
                msg = "✅ チャンネル制限を全て解除しました。"
        await interaction.response.send_message(msg, ephemeral=True)

    async def setup(bot):
        await bot.add_cog(Casino(bot))
