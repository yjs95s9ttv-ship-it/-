import discord
from discord import app_commands
from discord.ext import commands, tasks
import sqlite3
import random
import asyncio
from datetime import datetime

# --- ポイント配布イベント用View（永続・再起動対応） ---
class PointsEventView(discord.ui.View):
    """
    timeout=None + custom_id固定で永続View化。
    配布情報（amount・終了時刻・受け取り済み）はDBで管理するため
    再起動後もボタンが正常に動作する。
    """
    DB_PATH = "casino.db"

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="ポイントを受け取る！",
        style=discord.ButtonStyle.success,
        emoji="🎁",
        custom_id="points_event_claim",   # 固定IDで永続化
    )
    async def claim(self, interaction: discord.Interaction, button: discord.ui.Button):
        import time
        user_id = interaction.user.id

        with sqlite3.connect(self.DB_PATH) as conn:
            event = conn.execute(
                "SELECT amount, ends_at FROM point_events WHERE active=1 LIMIT 1"
            ).fetchone()

            if not event:
                await interaction.response.send_message("❌ 現在開催中の配布イベントはありません。", ephemeral=True)
                return

            amount, ends_at = event

            # 終了時刻チェック
            if ends_at is not None and time.time() > ends_at:
                await interaction.response.send_message("❌ このイベントは既に終了しています。", ephemeral=True)
                return

            # 受け取り済みチェック
            already = conn.execute(
                "SELECT 1 FROM point_event_claims WHERE user_id=?", (user_id,)
            ).fetchone()
            if already:
                await interaction.response.send_message("❌ 既に受け取り済みです。", ephemeral=True)
                return

            # ポイント付与
            guild_id = interaction.guild_id
            conn.execute(
                "INSERT OR IGNORE INTO users (guild_id, user_id, points) VALUES (?, ?, ?)",
                (guild_id, user_id, 100)
            )
            conn.execute(
                "UPDATE users SET points = points + ? WHERE guild_id = ? AND user_id = ?",
                (amount, guild_id, user_id)
            )
            conn.execute("INSERT INTO point_event_claims (user_id) VALUES (?)", (user_id,))
            conn.commit()

        await interaction.response.send_message(f"✅ **{amount}pt** を受け取りました！", ephemeral=True)


# ========================================================================
# 🃏 ブラックジャック用 UI (View)
# ========================================================================
class BlackjackView(discord.ui.View):
    def __init__(self, casino_cog, user_id, bet, guild_id):
        super().__init__(timeout=60.0)
        self.cog = casino_cog
        self.user_id = user_id
        self.bet = bet
        self.guild_id = guild_id
        
        self.deck = self._new_deck()
        random.shuffle(self.deck)
        
        self.player_hand = [self.draw_card(), self.draw_card()]
        self.dealer_hand = [self.draw_card(), self.draw_card()]
        # 最初から引き分けにならないように再抽選
        attempts = 0
        while (self.get_hand_total(self.player_hand) == self.get_hand_total(self.dealer_hand)
               and not self.is_blackjack(self.player_hand) and attempts < 10):
            self.deck = self._new_deck()
            random.shuffle(self.deck)
            self.player_hand = [self.draw_card(), self.draw_card()]
            self.dealer_hand = [self.draw_card(), self.draw_card()]
            attempts += 1

    def _new_deck(self):
        # 2〜10とA(=11)を均等に4枚ずつにして、出る数字に偏りが出ないようにする
        # （本物のトランプ通りだと10/J/Q/K=10扱いが16枚もあり、10ばかり出て見える）
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

    def make_embed(self, show_all_dealer=False):
        p_total = self.get_hand_total(self.player_hand)
        d_total = self.get_hand_total(self.dealer_hand)
        
        embed = discord.Embed(title="🃏 ブラックジャック (Blackjack)", color=discord.Color.blue())
        
        if show_all_dealer:
            d_cards = ", ".join([f"[{c if c != 11 else 'A'}]" for c in self.dealer_hand])
            embed.add_field(name=f"🤖 ディーラーの手札 (合計: {d_total})", value=d_cards, inline=False)
        else:
            embed.add_field(name="🤖 ディーラーの手札", value=f"[{self.dealer_hand[0] if self.dealer_hand[0] != 11 else 'A'}], [ ❓ ]", inline=False)

        p_cards = ", ".join([f"[{c if c != 11 else 'A'}]" for c in self.player_hand])
        embed.add_field(name=f"👤 あなたの手札 (合計: {p_total})", value=p_cards, inline=False)
        embed.set_footer(text=f"賭け金: {self.bet} pt")
        return embed

    async def end_game(self, interaction, msg_text, color, is_win=False):
        for btn in self.children:
            btn.disabled = True
        embed = self.make_embed(show_all_dealer=True)
        embed.color = color
        
        if is_win:
            embed.title = "✨🎉 勝利！WINNER 🎉✨"
        
        embed.description = msg_text
        await interaction.response.edit_message(embed=embed, view=self)
        self.stop()

    @discord.ui.button(label="ヒット (もう1枚引く)", style=discord.ButtonStyle.primary, emoji="➕")
    async def hit(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("❌ 他のユーザーのゲームです。", ephemeral=True)
            return

        self.player_hand.append(self.draw_card())
        p_total = self.get_hand_total(self.player_hand)

        if p_total > 21:
            self.cog.update_points(self.guild_id, self.user_id, -self.bet)
            total_pts = self.cog.get_points(self.guild_id, self.user_id)
            await self.end_game(
                interaction, 
                f"💥 **バスト！21を超えました！**\n{self.bet}pt を失いました。\n現在の所持: **{total_pts}pt**", 
                discord.Color.red(),
                is_win=False
            )
            return

        await interaction.response.edit_message(embed=self.make_embed(), view=self)

    @discord.ui.button(label="スタンド (勝負する)", style=discord.ButtonStyle.success, emoji="✋")
    async def stand(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("❌ 他のユーザーのゲームです。", ephemeral=True)
            return

        p_total = self.get_hand_total(self.player_hand)
        
        while self.get_hand_total(self.dealer_hand) < 17:
            self.dealer_hand.append(self.draw_card())

        d_total = self.get_hand_total(self.dealer_hand)

        if d_total > 21:
            payout = self.bet
            new_total = self.update_and_get_balance(payout)
            await self.end_game(interaction, f"🎉 **ディーラーがバストしました！あなたの勝ちです！**\n+{self.bet}pt 獲得！\n現在の所持: **{new_total}pt**", discord.Color.green(), is_win=True)
        elif p_total > d_total:
            payout = self.bet
            new_total = self.update_and_get_balance(payout)
            await self.end_game(interaction, f"🎉 **あなたの勝ちです！**\n+{self.bet}pt 獲得！\n現在の所持: **{new_total}pt**", discord.Color.green(), is_win=True)
        elif p_total < d_total:
            new_total = self.update_and_get_balance(-self.bet)
            await self.end_game(interaction, f"💀 **ディーラーの勝ちです...**\n{self.bet}pt を失いました。\n現在の所持: **{new_total}pt**", discord.Color.red(), is_win=False)
        else:
            await self.end_game(interaction, f"🤝 **引き分け（プッシュ）です。**\nポイントは戻されました。", discord.Color.greyple(), is_win=False)

    def update_and_get_balance(self, amount):
        self.cog.update_points(self.guild_id, self.user_id, amount)
        return self.cog.get_points(self.guild_id, self.user_id)


# ========================================================================
# 🎲 ハイアンドロー用 UI (View)
# ========================================================================
class HighLowView(discord.ui.View):
    def __init__(self, casino_cog, user_id, bet, guild_id):
        super().__init__(timeout=60.0)
        self.cog = casino_cog
        self.user_id = user_id
        self.bet = bet
        self.guild_id = guild_id
        self.streak = 0
        self.current_card = random.randint(1, 13)

    def get_card_name(self, num):
        if num == 1: return "A"
        if num == 11: return "J"
        if num == 12: return "Q"
        if num == 13: return "K"
        return str(num)

    def calculate_payout(self):
        rates = [1.5, 2.2, 3.5, 5.5, 10.0]
        idx = min(self.streak - 1, len(rates) - 1)
        return int(self.bet * rates[idx])

    def make_embed(self):
        embed = discord.Embed(title="🎲 ハイアンドロー (High & Low)", color=discord.Color.purple())
        embed.description = f"現在のカードより、次に出るカードが**大きいか(High)**、**小さいか(Low)**を当てろ！"
        embed.add_field(name="🎴 現在のカード", value=f"【 **{self.get_card_name(self.current_card)}** 】", inline=True)
        embed.add_field(name="🔥 現在の連勝数", value=f"**{self.streak} 連勝中**", inline=True)
        
        if self.streak > 0:
            embed.add_field(name="💰 現在の払い戻し予定", value=f"**{self.calculate_payout()} pt**\n(ここで『降りる』と獲得)", inline=False)
        else:
            embed.add_field(name="💰 元の賭け金", value=f"{self.bet} pt", inline=False)
            
        embed.set_footer(text="※カードの数字が今のカードと同じになることはありません。")
        return embed

    async def process_choice(self, interaction, guess):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("❌ 他のユーザーのゲームです。", ephemeral=True)
            return

        old_card = self.current_card
        
        rig_chance = random.random()
        force_lose = False

        # ──────────────────────────────────────────────
        # ⚙️ 【指定確率に上書き】連勝数ごとの確率操作
        # ──────────────────────────────────────────────
        if self.streak == 0:
            # 1回目：90%で当たるように設定（10%の確率で強制ハズレ）
            if rig_chance < 0.10: force_lose = True
        elif self.streak == 1:
            # 2回目：85%で当たるように設定（15%の確率で強制ハズレ）
            if rig_chance < 0.15: force_lose = True
        elif self.streak == 2:
            # 3回目：75%で当たるように設定（25%の確率で強制ハズレ）
            if rig_chance < 0.25: force_lose = True
        elif self.streak == 3:
            # 4回目：65%で当たるように設定（35%の確率で強制ハズレ）
            if rig_chance < 0.35: force_lose = True
        elif self.streak == 4:
            # 5回目（10倍確定）：50%で当たるように設定（50%の確率で強制ハズレ）
            if rig_chance < 0.50: force_lose = True

        if force_lose:
            if guess == "high":
                # old_card未満なら不正解になる → タイは除外して「より小さい」候補だけにする
                candidates = list(range(1, old_card))
            else:
                # old_cardより大きいなら不正解になる → タイは除外して「より大きい」候補だけにする
                candidates = list(range(old_card + 1, 14))

            if not candidates:
                # カードが端(A=1 や K=13)で強制ハズレの候補が作れない場合は
                # タイだけ除いた通常抽選にフォールバックする
                candidates = [c for c in range(1, 14) if c != old_card]
        else:
            # 通常抽選でもタイ(同じ数字)は絶対に出ないようにする
            candidates = [c for c in range(1, 14) if c != old_card]

        next_card = random.choice(candidates)

        is_correct = False
        if guess == "high" and next_card > old_card:
            is_correct = True
        elif guess == "low" and next_card < old_card:
            is_correct = True

        old_card_name = self.get_card_name(old_card)
        new_card_name = self.get_card_name(next_card)

        if is_correct:
            self.streak += 1
            self.current_card = next_card
            
            if self.streak >= 5:
                payout = self.calculate_payout()
                self.cog.update_points(self.guild_id, self.user_id, payout - self.bet)
                total_pts = self.cog.get_points(self.guild_id, self.user_id)
                
                for btn in self.children: btn.disabled = True
                embed = self.make_embed()
                # ✨ 金色ゴールド発光演出
                embed.color = discord.Color.from_rgb(255, 215, 0)
                embed.title = "✨🏆 金大当：神の領域（完全制覇） 🏆✨"
                embed.description = f"次に出たカード: 【 **{new_card_name}** 】(前: {old_card_name})\n\n🎉 50%の最終決戦を制し、見事**5連勝**を達成しました！\n賭け金が驚異の **10倍** に跳ね上がり、**{payout} pt** を獲得しました！\n現在の所持: **{total_pts}pt**"
                await interaction.response.edit_message(content=None, embed=embed, view=self)
                self.stop()
                return

            self.children[2].disabled = False
            await interaction.response.edit_message(content=f"✅ 的中！ 【{old_card_name}】の次は【**{new_card_name}**】でした！次の勝負へ進みますか？", embed=self.make_embed(), view=self)
        
        else:
            self.cog.update_points(self.guild_id, self.user_id, -self.bet)
            total_pts = self.cog.get_points(self.guild_id, self.user_id)
            
            for btn in self.children: btn.disabled = True
            embed = self.make_embed()
            embed.color = discord.Color.red()
            embed.description = f"💥 残念！ 【{old_card_name}】の次は【**{new_card_name}**】でした！\n\n{self.bet}pt を失いました。\n現在の所持: **{total_pts}pt**"
            await interaction.response.edit_message(content=None, embed=embed, view=self)
            self.stop()

    @discord.ui.button(label="High (大きい)", style=discord.ButtonStyle.primary, emoji="⬆️")
    async def high_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.process_choice(interaction, "high")

    @discord.ui.button(label="Low (小さい)", style=discord.ButtonStyle.danger, emoji="⬇️")
    async def low_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.process_choice(interaction, "low")

    @discord.ui.button(label="ここで降りる (報酬確定)", style=discord.ButtonStyle.success, emoji="💰", disabled=True)
    async def collect_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("❌ 他のユーザーのゲームです。", ephemeral=True)
            return

        payout = self.calculate_payout()
        self.cog.update_points(self.guild_id, self.user_id, payout - self.bet)
        total_pts = self.cog.get_points(self.guild_id, self.user_id)
        
        for btn in self.children: btn.disabled = True
        embed = self.make_embed()
        embed.color = discord.Color.green()
        embed.description = f"💰 ゲームを終了し、手堅く利確しました！\n**{payout} pt** 獲得！\n現在の所持: **{total_pts}pt**"
        await interaction.response.edit_message(content=None, embed=embed, view=self)
        self.stop()


# ========================================================================
# 🃏 ポーカー用 UI (View)
# ========================================================================
HAND_RANKS = {
    "ロイヤルストレートフラッシュ": 9,
    "ストレートフラッシュ":         8,
    "フォーカード":                 7,
    "フルハウス":                   6,
    "フラッシュ":                   5,
    "ストレート":                   4,
    "スリーカード":                 3,
    "ツーペア":                     2,
    "ワンペア":                     1,
    "ハイカード":                   0,
}
HAND_PAYOUT = {
    "ロイヤルストレートフラッシュ": 50,
    "ストレートフラッシュ":         20,
    "フォーカード":                 10,
    "フルハウス":                   6,
    "フラッシュ":                   5,
    "ストレート":                   4,
    "スリーカード":                 3,
    "ツーペア":                     2,
    "ワンペア":                     1,
    "ハイカード":                   0,
}

SUITS = ["♠", "♥", "♦", "♣"]
RANKS = ["A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K"]

def new_poker_deck():
    return [(s, r) for s in SUITS for r in RANKS]

def card_str(card):
    return f"{card[0]}{card[1]}"

def rank_value(r):
    return RANKS.index(r)

def evaluate_hand(hand):
    """5枚の手札 [(suit, rank), ...] を評価して役名を返す"""
    suits = [c[0] for c in hand]
    ranks = [c[1] for c in hand]
    vals = sorted([rank_value(r) for r in ranks])

    is_flush = len(set(suits)) == 1
    is_straight = (vals == list(range(vals[0], vals[0] + 5))) or (vals == [0, 1, 2, 3, 12])  # A-2-3-4-5

    from collections import Counter
    counts = Counter(vals)
    freq = sorted(counts.values(), reverse=True)

    if is_flush and is_straight:
        if vals == [8, 9, 10, 11, 12] or (set(ranks) == {"10", "J", "Q", "K", "A"}):
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


class PokerView(discord.ui.View):
    def __init__(self, casino_cog, user_id, bet, guild_id):
        super().__init__(timeout=60.0)
        self.cog = casino_cog
        self.user_id = user_id
        self.bet = bet
        self.guild_id = guild_id
        self.phase = "draw"  # draw → result
        self.held = [False] * 5  # どのカードをキープするか

        deck = new_poker_deck()
        random.shuffle(deck)
        self.hand = [deck.pop() for _ in range(5)]
        self.deck = deck

        self._update_buttons()

    def _update_buttons(self):
        self.clear_items()
        if self.phase == "draw":
            for i in range(5):
                label = f"{'🔒' if self.held[i] else '　'} {card_str(self.hand[i])}"
                btn = discord.ui.Button(
                    label=label,
                    style=discord.ButtonStyle.success if self.held[i] else discord.ButtonStyle.secondary,
                    row=0,
                    custom_id=f"hold_{i}",
                )
                btn.callback = self._make_hold_callback(i)
                self.add_item(btn)
            deal_btn = discord.ui.Button(label="🃏 交換してゲーム終了", style=discord.ButtonStyle.primary, row=1)
            deal_btn.callback = self.deal_callback
            self.add_item(deal_btn)

    def _make_hold_callback(self, idx):
        async def callback(interaction: discord.Interaction):
            if interaction.user.id != self.user_id:
                await interaction.response.send_message("❌ 他のユーザーのゲームです。", ephemeral=True)
                return
            self.held[idx] = not self.held[idx]
            self._update_buttons()
            await interaction.response.edit_message(embed=self.make_embed(), view=self)
        return callback

    async def deal_callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("❌ 他のユーザーのゲームです。", ephemeral=True)
            return
        # キープしていないカードを交換
        for i in range(5):
            if not self.held[i]:
                self.hand[i] = self.deck.pop()
        self.phase = "result"
        self._update_buttons()

        hand_name = evaluate_hand(self.hand)
        multiplier = HAND_PAYOUT[hand_name]

        if multiplier > 0:
            payout = self.bet * multiplier
            net = payout - self.bet
            new_total = self.cog.update_points(self.guild_id, self.user_id, net)
            if multiplier >= 20:
                color = discord.Color.from_rgb(255, 215, 0)
                title = f"✨🏆 {hand_name}！！ 🏆✨"
            elif multiplier >= 5:
                color = discord.Color.from_rgb(255, 0, 127)
                title = f"🎉 {hand_name}！"
            else:
                color = discord.Color.green()
                title = f"✅ {hand_name}"
            desc = f"**{payout}pt** 獲得！（{multiplier}倍）\n現在の所持: **{new_total}pt**"
        else:
            new_total = self.cog.update_points(self.guild_id, self.user_id, -self.bet)
            color = discord.Color.red()
            title = "💀 ハイカード（役なし）"
            desc = f"{self.bet}pt を失いました。\n現在の所持: **{new_total}pt**"

        embed = self.make_embed(result_title=title, result_desc=desc)
        embed.color = color
        await interaction.response.edit_message(embed=embed, view=self)
        self.stop()

    def make_embed(self, result_title=None, result_desc=None):
        hand_str = "  ".join(card_str(c) for c in self.hand)
        held_str = "  ".join("🔒" if h else "　" for h in self.held)

        if result_title:
            embed = discord.Embed(title=result_title, color=discord.Color.blue())
            embed.add_field(name="最終手札", value=f"`{hand_str}`", inline=False)
            embed.description = result_desc
        else:
            embed = discord.Embed(title="🃏 ポーカー (5枚交換)", color=discord.Color.blue())
            embed.description = "キープしたいカードを押してロック🔒してから「交換してゲーム終了」を押してください。"
            embed.add_field(name="手札", value=f"`{hand_str}`", inline=False)
            embed.add_field(name="キープ状態", value=f"`{held_str}`", inline=False)
        embed.set_footer(text=f"賭け金: {self.bet}pt　｜　ワンペア×1 / ツーペア×2 / スリーカード×3 / ストレート×4 / フラッシュ×5 / フルハウス×6 / フォーカード×10 / SF×20 / RSF×50")
        return embed


# ========================================================================
# 🎰 メイン カジノシステム Cog クラス
# ========================================================================
class Casino(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.db_path = "casino.db"
        self._init_db()
        self.ranking_messages = []
        # 永続Viewを登録（再起動後もボタンを受け取れるように）
        self.bot.add_view(PointsEventView())
        self.update_ranking_task.start()
        self.reset_inactive_task.start()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    guild_id     INTEGER NOT NULL,
                    user_id      INTEGER NOT NULL,
                    points       INTEGER DEFAULT 100,
                    last_daily   TEXT,
                    last_updated TEXT,
                    PRIMARY KEY (guild_id, user_id)
                )
            """)
            # 旧テーブル（guild_id なし）からの移行
            try:
                cols = [c[1] for c in conn.execute("PRAGMA table_info(users)").fetchall()]
                if "guild_id" not in cols:
                    conn.execute("ALTER TABLE users ADD COLUMN guild_id INTEGER NOT NULL DEFAULT 0")
                    conn.commit()
                    print("[casino] users テーブルに guild_id カラムを追加しました（既存データは guild_id=0）")
            except Exception as e:
                print(f"[casino] users migration error: {e}")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS casino_channels (
                    guild_id   INTEGER NOT NULL,
                    channel_id INTEGER NOT NULL,
                    PRIMARY KEY (guild_id, channel_id)
                )
            """)
            # ポイント配布イベント管理
            conn.execute("""
                CREATE TABLE IF NOT EXISTS point_events (
                    id       INTEGER PRIMARY KEY AUTOINCREMENT,
                    amount   INTEGER NOT NULL,
                    ends_at  REAL,       -- UNIXタイムスタンプ（NULLなら無期限）
                    active   INTEGER DEFAULT 1
                )
            """)
            # 受け取り済みユーザー管理
            conn.execute("""
                CREATE TABLE IF NOT EXISTS point_event_claims (
                    user_id  INTEGER PRIMARY KEY
                )
            """)
            try:
                conn.execute("ALTER TABLE users ADD COLUMN last_updated TEXT")
            except:
                pass

            # casino_channels テーブルの構造移行（古い guild_id PRIMARY KEY → 複合キー）
            try:
                info = conn.execute("PRAGMA table_info(casino_channels)").fetchall()
                col_names = [col[1] for col in info]
                if col_names == ["guild_id", "channel_id"]:
                    # PRIMARY KEY が guild_id 単体かチェック
                    pk_cols = [col[1] for col in info if col[5] > 0]
                    if pk_cols == ["guild_id"]:
                        # 古い構造なので移行
                        existing = conn.execute("SELECT guild_id, channel_id FROM casino_channels").fetchall()
                        conn.execute("DROP TABLE casino_channels")
                        conn.execute("""
                            CREATE TABLE casino_channels (
                                guild_id   INTEGER NOT NULL,
                                channel_id INTEGER NOT NULL,
                                PRIMARY KEY (guild_id, channel_id)
                            )
                        """)
                        for row in existing:
                            conn.execute("INSERT OR IGNORE INTO casino_channels (guild_id, channel_id) VALUES (?,?)", row)
                        print("[casino] casino_channels テーブルを新構造に移行しました。")
            except Exception as e:
                print(f"[casino] migration error: {e}")

            conn.commit()

    def get_casino_channels(self, guild_id: int) -> list[int]:
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT channel_id FROM casino_channels WHERE guild_id=?", (guild_id,)
            ).fetchall()
        return [r[0] for r in rows]

    def is_casino_channel(self, message) -> bool:
        """カジノチャンネルが未設定 or 設定済みチャンネルのどれかと一致する場合True"""
        if message.guild is None:
            return False
        allowed = self.get_casino_channels(message.guild.id)
        if not allowed:
            return True  # 未設定なら全チャンネルOK
        return message.channel.id in allowed

    def get_points(self, guild_id, user_id):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT points FROM users WHERE guild_id = ? AND user_id = ?", (guild_id, user_id))
            row = cursor.fetchone()
            if row:
                return row[0]
            else:
                conn.execute("INSERT INTO users (guild_id, user_id, points) VALUES (?, ?, ?)", (guild_id, user_id, 100))
                conn.commit()
                return 100

    def update_points(self, guild_id, user_id, amount):
        current = self.get_points(guild_id, user_id)
        new_total = max(0, current + amount)
        now = datetime.now().strftime('%Y-%m-%d')
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "UPDATE users SET points = ?, last_updated = ? WHERE guild_id = ? AND user_id = ?",
                (new_total, now, guild_id, user_id)
            )
            conn.commit()
        return new_total

    def create_ranking_embed(self, guild_id: int):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT user_id, points FROM users WHERE guild_id = ? ORDER BY points DESC LIMIT 10",
                (guild_id,)
            )
            rows = cursor.fetchall()
        embed = discord.Embed(title="🏆 ポイントランキング TOP10", color=discord.Color.gold())
        now = datetime.now().strftime('%H:%M')
        for i, (u_id, pts) in enumerate(rows, 1):
            user = self.bot.get_user(u_id)
            name = user.display_name if user else f"不明({u_id})"
            embed.add_field(name=f"{i}位: {name}", value=f"{pts} pt", inline=False)
        embed.set_footer(text=f"最終更新: {now} (10分ごとに更新)")
        return embed

    @tasks.loop(minutes=10)
    async def update_ranking_task(self):
        for entry in self.ranking_messages[:]:
            msg, guild_id = entry
            try:
                embed = self.create_ranking_embed(guild_id)
                await msg.edit(embed=embed)
            except:
                self.ranking_messages.remove(entry)

    @tasks.loop(hours=24)
    async def reset_inactive_task(self):
        from datetime import timedelta
        threshold = (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d')
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                UPDATE users SET points = 100
                WHERE (last_updated IS NULL OR last_updated < ?)
                AND points != 100
            """, (threshold,))
            conn.commit()

    @app_commands.command(name="daily", description="1日1回ログインボーナスを受け取ります")
    async def daily(self, interaction: discord.Interaction):
        user_id  = interaction.user.id
        guild_id = interaction.guild_id
        now = datetime.now()
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT last_daily FROM users WHERE guild_id = ? AND user_id = ?", (guild_id, user_id))
            row = cursor.fetchone()
            if row and row[0]:
                last_date = datetime.strptime(row[0], '%Y-%m-%d')
                if last_date.date() >= now.date():
                    await interaction.response.send_message("❌ 今日は既に受け取り済みです。また明日お越しください！", ephemeral=True)
                    return
            self.update_points(guild_id, user_id, 100)
            conn.execute(
                "UPDATE users SET last_daily = ? WHERE guild_id = ? AND user_id = ?",
                (now.strftime('%Y-%m-%d'), guild_id, user_id)
            )
            conn.commit()
        await interaction.response.send_message(f"💰 **100pt** 獲得しました！明日のログインもお待ちしています。")

    @app_commands.command(name="roulette", description="倍率勝負！ポイントを賭けて増やそう")
    async def roulette(self, interaction: discord.Interaction, bet: int, rate: int = 2):
        if bet <= 0 or rate < 2:
            await interaction.response.send_message("❌ 正しい数値を入力してください。", ephemeral=True)
            return
        guild_id = interaction.guild_id
        current = self.get_points(guild_id, interaction.user.id)
        if current < bet:
            await interaction.response.send_message(f"❌ ポイントが足りません。(所持: {current}pt)", ephemeral=True)
            return
        
        win = random.random() < (1.0 / rate) * 0.8
        
        frames = ["🟢", "🔴", "🟡", "🔵", "🟣", "🟠", "⚫", "⚪"]
        embed = discord.Embed(title="🎰 ルーレット起動...", color=discord.Color.dark_theme())
        embed.description = "🟢 ルーレットが激しく回転中...\n【 🔄 🔄 🔄 🔄 🔄 】"
        await interaction.response.send_message(embed=embed)
        
        for i in range(3):
            await asyncio.sleep(0.6)
            random_frames = "".join(random.sample(frames, 5))
            embed.description = f"⚡ ルーレット回転中... [ 満ち引き of 運命 ]\n【 {random_frames} 】"
            await interaction.edit_original_response(embed=embed)

        await asyncio.sleep(0.6)

        if win:
            payout = bet * (rate - 1)
            total = self.update_points(guild_id, interaction.user.id, payout)
            embed.title = "✨🎰 超大当：JACKPOT 🎰✨"
            embed.color = discord.Color.from_rgb(255, 0, 127)
            embed.description = f"🌟 **🎯 ズバッ！大当たりの数字を引き当てました！**\n\n**{rate}倍** の配当が適用され、**{bet * rate}pt** になりました！\n現在の所持: **{total}pt**"
        else:
            total = self.update_points(guild_id, interaction.user.id, -bet)
            embed.title = "💀 ハズレ... 💀"
            embed.color = discord.Color.red()
            embed.description = f"惨敗... 吸い込まれるようにハズレの枠に落ちました。\n\n**{bet}pt** を失いました。\n現在の所持: **{total}pt**"
            
        await interaction.edit_original_response(embed=embed)

    @app_commands.command(name="blackjack", description="ブラックジャックでBotのディーラーと勝負！")
    async def blackjack(self, interaction: discord.Interaction, bet: int):
        if bet <= 0:
            await interaction.response.send_message("❌ 1pt以上を賭けてください。", ephemeral=True)
            return
        guild_id = interaction.guild_id
        current = self.get_points(guild_id, interaction.user.id)
        if current < bet:
            await interaction.response.send_message(f"❌ ポイントが足りません。(所持: {current}pt)", ephemeral=True)
            return

        view = BlackjackView(self, interaction.user.id, bet, guild_id)
        if view.is_blackjack(view.player_hand):
            bj_payout = int(bet * 1.5)
            total = self.update_points(guild_id, interaction.user.id, bj_payout)
            embed = view.make_embed(show_all_dealer=True)
            embed.title = "✨🃏 超大当：BLACKJACK 🃏✨"
            embed.color = discord.Color.from_rgb(255, 215, 0)
            embed.description = f"✨🃏 **ブラックジャック！！！** 即勝利しました！\n+{bj_payout}pt 獲得！\n現在の所持: **{total}pt**"
            await interaction.response.send_message(embed=embed)
            return

        await interaction.response.send_message(embed=view.make_embed(), view=view)

    @app_commands.command(name="highlow", description="トランプの大小を当てろ！5連勝すれば倍率は驚異の10倍！")
    async def highlow(self, interaction: discord.Interaction, bet: int):
        if bet <= 0:
            await interaction.response.send_message("❌ 1pt以上を賭けてください。", ephemeral=True)
            return
        guild_id = interaction.guild_id
        current = self.get_points(guild_id, interaction.user.id)
        if current < bet:
            await interaction.response.send_message(f"❌ ポイントが足りません。(所持: {current}pt)", ephemeral=True)
            return

        view = HighLowView(self, interaction.user.id, bet, guild_id)
        await interaction.response.send_message(embed=view.make_embed(), view=view)

    # ==========================================
    # 🔒 管理者（Administrator）専用コマンド
    # ==========================================

    @app_commands.command(name="setup_ranking", description="【管理者用】ランキングパネル設置")
    @app_commands.default_permissions(administrator=True)
    async def setup_ranking(self, interaction: discord.Interaction):
        embed = self.create_ranking_embed(interaction.guild_id)
        await interaction.response.send_message("ランキングパネルを設置しました。", ephemeral=True)
        message = await interaction.channel.send(embed=embed)
        self.ranking_messages.append((message, interaction.guild_id))

    @app_commands.command(name="reset_points", description="【管理者用】ポイントリセット")
    @app_commands.default_permissions(administrator=True)
    async def reset_points(self, interaction: discord.Interaction, target: discord.Member = None):
        guild_id = interaction.guild_id
        with sqlite3.connect(self.db_path) as conn:
            if target:
                conn.execute("UPDATE users SET points = 0 WHERE guild_id = ? AND user_id = ?", (guild_id, target.id))
                msg = f"✅ {target.mention} を 0pt に戻しました。"
            else:
                conn.execute("UPDATE users SET points = 0 WHERE guild_id = ?", (guild_id,))
                msg = "✅ 全員のポイントを 0pt にリセットしました。"
            conn.commit()
        await interaction.response.send_message(msg)

    @app_commands.command(name="event_points", description="【管理者用】ポイント配布開催！")
    @app_commands.default_permissions(administrator=True)
    async def event_points(self, interaction: discord.Interaction, amount: int, minutes: float):
        import time

        ends_at = time.time() + minutes * 60

        # 既存イベントを終了してから新規作成
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("UPDATE point_events SET active=0")
            conn.execute("DELETE FROM point_event_claims")
            conn.execute(
                "INSERT INTO point_events (amount, ends_at, active) VALUES (?,?,1)",
                (amount, ends_at)
            )
            conn.commit()

        view = PointsEventView()
        await interaction.response.send_message(f"✅ {minutes}分間の配布を開始しました。", ephemeral=True)
        embed = discord.Embed(
            title="🎁 ポイント配布開催！！",
            description=(
                f"ボタンを押してポイントを受け取ろう！\n\n"
                f"💰 配布ポイント: **{amount} pt**\n"
                f"⏱️ 開催時間: **{minutes} 分間**"
            ),
            color=discord.Color.green()
        )
        msg = await interaction.channel.send(embed=embed, view=view)

        # 終了まで待機してパネルを締め切り
        await asyncio.sleep(minutes * 60)

        with sqlite3.connect(self.db_path) as conn:
            conn.execute("UPDATE point_events SET active=0")
            conn.commit()

        await msg.edit(
            embed=discord.Embed(title="🏁 配布イベント終了", color=discord.Color.red()),
            view=None,
        )

    @app_commands.command(name="give_points", description="【管理者用】ポイント付与")
    @app_commands.default_permissions(administrator=True)
    async def give_points(self, interaction: discord.Interaction, amount: int, target: discord.Member = None):
        guild_id = interaction.guild_id
        if target:
            self.update_points(guild_id, target.id, amount)
            await interaction.response.send_message(f"✅ {target.mention} に {amount}pt 付与。")
        else:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("UPDATE users SET points = points + ? WHERE guild_id = ?", (amount, guild_id))
                conn.commit()
            await interaction.response.send_message(f"✅ 全員に {amount}pt 付与。")

    @app_commands.command(name="set_casino_channel", description="【管理者用】カジノコマンド(acc/abb/add/aee)を使えるチャンネルを指定します")
    @app_commands.default_permissions(administrator=True)
    async def set_casino_channel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT OR IGNORE INTO casino_channels (guild_id, channel_id) VALUES (?, ?)
            """, (interaction.guild.id, channel.id))
            conn.commit()
        await interaction.response.send_message(
            f"✅ カジノチャンネルを {channel.mention} に設定しました。\n"
            f"`acc` `abb` `add` `aee` はこのチャンネルのみで使用できます。",
            ephemeral=True,
        )

    @app_commands.command(name="unset_casino_channel", description="【管理者用】カジノチャンネルを削除します（channel未指定で全解除）")
    @app_commands.default_permissions(administrator=True)
    async def unset_casino_channel(self, interaction: discord.Interaction, channel: discord.TextChannel = None):
        with sqlite3.connect(self.db_path) as conn:
            if channel:
                conn.execute("DELETE FROM casino_channels WHERE guild_id=? AND channel_id=?", (interaction.guild.id, channel.id))
                conn.commit()
                remaining = self.get_casino_channels(interaction.guild.id)
                if remaining:
                    mentions = " ".join(f"<#{cid}>" for cid in remaining)
                    msg = f"✅ {channel.mention} を解除しました。残りのカジノチャンネル: {mentions}"
                else:
                    msg = f"✅ {channel.mention} を解除しました。全チャンネルでカジノコマンドが使用できます。"
            else:
                conn.execute("DELETE FROM casino_channels WHERE guild_id=?", (interaction.guild.id,))
                conn.commit()
                msg = "✅ チャンネル制限を全て解除しました。全チャンネルでカジノコマンドが使用できます。"
        await interaction.response.send_message(msg, ephemeral=True)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return

        content = message.content.strip()
        parts = content.split()

        if len(parts) < 2:
            return

        prefix = parts[0].lower()
        if prefix not in ("acc", "abb", "add", "aee"):
            return

        # カジノチャンネルチェック
        if not self.is_casino_channel(message):
            allowed_ids = self.get_casino_channels(message.guild.id)
            mentions = " ".join(f"<#{cid}>" for cid in allowed_ids)
            await message.reply(
                f"❌ カジノコマンドは {mentions} でのみ使用できます。",
                delete_after=5,
                mention_author=False,
            )
            return

        try:
            bet = int(parts[1])
        except ValueError:
            await message.channel.send("❌ ベット数は整数で入力してください。", delete_after=5)
            return

        if bet < 50 or bet > 1000:
            await message.channel.send("❌ ベットは **50pt〜1000pt** の範囲で入力してください。", delete_after=5)
            return

        user_id  = message.author.id
        guild_id = message.guild.id
        current = self.get_points(guild_id, user_id)
        if current < bet:
            await message.channel.send(f"❌ ポイントが足りません。(所持: {current}pt)", delete_after=5)
            return

        if prefix == "acc":
            view = BlackjackView(self, user_id, bet, guild_id)
            if view.is_blackjack(view.player_hand):
                bj_payout = int(bet * 1.5)
                total = self.update_points(guild_id, user_id, bj_payout)
                embed = view.make_embed(show_all_dealer=True)
                embed.title = "✨🃏 超大当：BLACKJACK 🃏✨"
                embed.color = discord.Color.from_rgb(255, 215, 0)
                embed.description = f"✨🃏 **ブラックジャック！！！** 即勝利しました！\n+{bj_payout}pt 獲得！\n現在の所持: **{total}pt**"
                await message.channel.send(embed=embed)
                return
            await message.channel.send(embed=view.make_embed(), view=view)

        elif prefix == "abb":
            rate = int(parts[2]) if len(parts) >= 3 and parts[2].isdigit() else 2
            if rate < 2:
                await message.channel.send("❌ 倍率は2以上で入力してください。", delete_after=5)
                return
            win = __import__("random").random() < (1.0 / rate) * 0.8
            frames = ["🟢", "🔴", "🟡", "🔵", "🟣", "🟠", "⚫", "⚪"]
            import random
            embed = discord.Embed(title="🎰 ルーレット起動...", color=discord.Color.dark_theme())
            embed.description = "🟢 ルーレットが激しく回転中...\n【 🔄 🔄 🔄 🔄 🔄 】"
            msg = await message.channel.send(embed=embed)
            for i in range(3):
                await asyncio.sleep(0.6)
                random_frames = "".join(random.sample(frames, 5))
                embed.description = f"⚡ ルーレット回転中... [ 満ち引き of 運命 ]\n【 {random_frames} 】"
                await msg.edit(embed=embed)
            await asyncio.sleep(0.6)
            if win:
                payout = bet * (rate - 1)
                total = self.update_points(guild_id, user_id, payout)
                embed.title = "✨🎰 超大当：JACKPOT 🎰✨"
                embed.color = discord.Color.from_rgb(255, 0, 127)
                embed.description = f"🌟 **🎯 ズバッ！大当たりの数字を引き当てました！**\n\n**{rate}倍** の配当が適用され、**{bet * rate}pt** になりました！\n現在の所持: **{total}pt**"
            else:
                total = self.update_points(guild_id, user_id, -bet)
                embed.title = "💀 ハズレ... 💀"
                embed.color = discord.Color.red()
                embed.description = f"惨敗... 吸い込まれるようにハズレの枠に落ちました。\n\n**{bet}pt** を失いました。\n現在の所持: **{total}pt**"
            await msg.edit(embed=embed)

        elif prefix == "add":
            view = HighLowView(self, user_id, bet, guild_id)
            await message.channel.send(embed=view.make_embed(), view=view)

        elif prefix == "aee":
            view = PokerView(self, user_id, bet, guild_id)
            await message.channel.send(embed=view.make_embed(), view=view)


async def setup(bot):
    await bot.add_cog(Casino(bot))