import discord
from discord.ext import commands
from discord import app_commands
import sys
import os
import json
from pathlib import Path

BCSFE_PATH = os.path.join(os.path.dirname(__file__), 'BCSFE-Python-main', 'src')
sys.path.insert(0, BCSFE_PATH)

from bcsfe import core

ALLOWED_USERS = [1465368277663350794]
BASE_DIR = Path("/home/container")
FALLBACK_BASE_DIR = Path(__file__).resolve().parent.parent
RENTAL_DB_PATH = BASE_DIR / "rental.db"
LICENSE_DB_PATH = BASE_DIR / "license.db"


def _resolve_db_path(primary: Path, fallback_name: str) -> Path:
    if primary.exists():
        return primary
    return FALLBACK_BASE_DIR / fallback_name


def _check_rental_permission(guild_id: int | None, user_id: int) -> bool:
    """main側のrental.dbを参照して貸し出し権限を確認"""
    if guild_id is None:
        return False
    try:
        import sqlite3
        db_path = _resolve_db_path(RENTAL_DB_PATH, "rental.db")
        if not db_path.exists():
            return False
        with sqlite3.connect(str(db_path)) as conn:
            row = conn.execute(
                "SELECT 1 FROM rentals WHERE guild_id=? AND (user_id=? OR user_id=0)",
                (guild_id, user_id)
            ).fetchone()
        return row is not None
    except Exception:
        return False


def _check_license(guild_id: int, command_name: str) -> bool:
    """server_license.pyのlicense.dbを参照してライセンスチェック"""
    try:
        import sqlite3, time
        db_path = _resolve_db_path(LICENSE_DB_PATH, "license.db")
        if not db_path.exists():
            return False
        with sqlite3.connect(str(db_path)) as conn:
            cur = conn.execute(
                "SELECT plan, expiry_timestamp FROM server_licenses WHERE guild_id=?",
                (guild_id,)
            )
            row = cur.fetchone()
        if not row:
            return False
        plan, expiry = row
        if expiry and expiry < int(time.time()):
            return False
        plan_a = {"daiko"}
        plan_b = {"daiko", "daiko_cats", "daiko_account", "daiko_custom", "daiko_account_save"}
        if plan == "A":
            return command_name in plan_a
        if plan == "B":
            return command_name in plan_b
        return False
    except Exception:
        return False


def _can_use_daiko_command(interaction: discord.Interaction, command_name: str) -> bool:
    if interaction.user.id in ALLOWED_USERS:
        return True
    if _check_rental_permission(interaction.guild_id, interaction.user.id):
        return True
    return _check_license(interaction.guild_id, command_name)


PANELS_FILE = Path(__file__).parent / "panels.json"

TEMPLATE_TC          = "7fd88e6af"
TEMPLATE_CC          = "1430"
TEMPLATE_BACKUP_PATH = "template_save.bin"
TEMPLATE_CODES_FILE  = "template_codes.json"

def load_panels():
    if PANELS_FILE.exists():
        try:
            with open(PANELS_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            return {}
    return {}

def save_panels(panels_data):
    try:
        with open(PANELS_FILE, 'w', encoding='utf-8') as f:
            json.dump(panels_data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"パネル保存エラー: {e}")

def add_panel(message_id, channel_id, achievement_channel_id=None, required_role_id=None):
    panels = load_panels()
    panels[str(message_id)] = {
        "channel_id": channel_id,
        "achievement_channel_id": achievement_channel_id,
        "required_role_id": required_role_id
    }
    save_panels(panels)

def remove_panel(message_id):
    panels = load_panels()
    if str(message_id) in panels:
        del panels[str(message_id)]
        save_panels(panels)

def _get_template_codes() -> tuple[str, str]:
    if os.path.exists(TEMPLATE_CODES_FILE):
        try:
            with open(TEMPLATE_CODES_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            tc = data.get("tc", TEMPLATE_TC)
            cc = data.get("cc", TEMPLATE_CC)
            if tc and cc:
                return tc, cc
        except Exception:
            pass
    return TEMPLATE_TC, TEMPLATE_CC

def _save_template_codes(tc: str, cc: str):
    try:
        with open(TEMPLATE_CODES_FILE, "w", encoding="utf-8") as f:
            json.dump({"tc": tc, "cc": cc}, f, ensure_ascii=False)
    except Exception:
        pass

def _load_template_save(cc, gv):
    """テンプレートのベースセーブデータを読み込むヘルパー"""
    import traceback as _tb
    # ローカルキャッシュが存在すれば使う
    if os.path.exists(TEMPLATE_BACKUP_PATH):
        try:
            with open(TEMPLATE_BACKUP_PATH, "rb") as f:
                data = f.read()
            sf = core.SaveFile(core.Data(data), cc=cc)
            print("[template] ローカルキャッシュから読み込み成功")
            return sf
        except Exception as e:
            print(f"[template] キャッシュ読み込み失敗、サーバーから再取得します: {e}")
            # 壊れたキャッシュを削除
            try:
                os.remove(TEMPLATE_BACKUP_PATH)
            except Exception:
                pass

    tc, cc_str = _get_template_codes()
    print(f"[template] サーバーから取得開始 tc={tc} cc={cc_str}")
    try:
        res = core.ServerHandler.from_codes(
            tc, cc_str, cc, gv, print=False, save_backup=False)
    except Exception as e:
        print(f"[template] from_codes 例外: {e}\n{_tb.format_exc()}")
        return None

    if not res or not res[0]:
        req_result = res[1] if res and len(res) > 1 else None
        if req_result:
            attrs = {k: v for k, v in vars(req_result).items() if not k.startswith('__')}
            print(f"[template] from_codes 失敗 RequestResult attrs={attrs}")
            resp = getattr(req_result, 'response', None)
            if resp is not None:
                try:
                    print(f"[template] response body: {resp.text}")
                except Exception as e:
                    print(f"[template] response.text 取得失敗: {e}")
        else:
            print(f"[template] from_codes が None を返しました res={res}")
        return None

    handler = res[0]
    sf = handler.save_file
    if sf is None:
        print("[template] handler.save_file が None です")
        return None

    print("[template] サーバーからの取得成功、コード更新中...")
    try:
        new_codes = handler.get_codes(upload_managed_items=False)
        if new_codes:
            _save_template_codes(new_codes[0], new_codes[1])
            print(f"[template] 新コード保存: {new_codes[0]}")
        else:
            print("[template] get_codes が None を返しました（コード更新スキップ）")
    except Exception as e:
        print(f"[template] コード更新失敗: {e}")

    try:
        raw = sf.to_data().to_bytes()
        with open(TEMPLATE_BACKUP_PATH, "wb") as f:
            f.write(raw)
        print("[template] ローカルキャッシュを保存しました")
    except Exception as e:
        print(f"[template] キャッシュ保存失敗: {e}")

    return sf

def claim_user_rank_rewards(save):
    try:
        if hasattr(save, 'user_rank_rewards'):
            for reward in save.user_rank_rewards.rewards:
                reward.claimed = True
    except:
        pass
        
def clear_beacon_events(save):
    try:
        if hasattr(save, 'beacon_event_list_scene') and save.beacon_event_list_scene:
            for key in save.beacon_event_list_scene.bool_array.keys():
                save.beacon_event_list_scene.bool_array[key] = True
    except:
        pass
        
def clear_item_packs(save):
    try:
        if hasattr(save, 'item_pack') and save.item_pack:
            for key in save.item_pack.displayed_packs.keys():
                save.item_pack.displayed_packs[key] = True
            save.item_pack.three_days_started = False
    except:
        pass    
        
def clear_scheme_items(save):
    try:
        if hasattr(save, 'scheme_items') and save.scheme_items:
            save.scheme_items.to_obtain = []
    except:
        pass        
        
def clear_all_ads_and_popups(save, is_aku_clear: bool):
    try:
        for cat in save.cats.cats:
            cat.gatya_seen = 1
            cat.catguide_collected = True
        
        if is_aku_clear:
            if hasattr(save, 'unlock_popups') and save.unlock_popups:
                # 既存のポップアップのみseenにする（全ID新規追加すると章解放ログが壊れる）
                for popup_id in list(save.unlock_popups.popups.keys()):
                    save.unlock_popups.popups[popup_id].seen = True
            
            if hasattr(save, 'unlock_popups_0'):
                save.unlock_popups_0 = [1] * len(save.unlock_popups_0)
            if hasattr(save, 'unlock_popups_6'):
                save.unlock_popups_6 = [True] * len(save.unlock_popups_6)
            if hasattr(save, 'unlock_popups_8'):
                save.unlock_popups_8 = [1] * len(save.unlock_popups_8)
            if hasattr(save, 'unlock_popups_11'):
                save.unlock_popups_11 = [1] * len(save.unlock_popups_11)
        
        if hasattr(save, 'user_rank_rewards') and save.user_rank_rewards:
            for reward in save.user_rank_rewards.rewards:
                reward.claimed = True
        
        if hasattr(save, 'mysale') and save.mysale:
            for key in range(10000):
                save.mysale.dict_2[key] = True
        
        if hasattr(save, 'rank_up_sale_value'):
            save.rank_up_sale_value = 0x7FFFFFFF
        
        if hasattr(save, 'announcements'):
            save.announcements = []
        
        if hasattr(save, 'shown_maxcollab_mg'):
            save.shown_maxcollab_mg = True
        
        if hasattr(save, 'energy_notification'):
            save.energy_notification = False
        
        if hasattr(save, 'beacon_base') and save.beacon_base:
            for key in save.beacon_base.bool_array.keys():
                save.beacon_base.bool_array[key] = True
        
        if hasattr(save, 'item_pack') and save.item_pack:
            for key in save.item_pack.displayed_packs.keys():
                save.item_pack.displayed_packs[key] = True
            save.item_pack.three_days_started = False
        
        if hasattr(save, 'scheme_items') and save.scheme_items:
            save.scheme_items.to_obtain = []
        
        if hasattr(save, 'show_ban_message'):
            save.show_ban_message = False
        
        if hasattr(save, 'event_update_flags'):
            save.event_update_flags = True
            
    except Exception as e:
        print(f"Error in clear_all_ads_and_popups: {e}")
        pass

def _fix_empty_lists_for_new_account(sf):
    if not isinstance(getattr(sf, "unlock_enemy_guide", 0), int):
        sf.unlock_enemy_guide = 0
    if not isinstance(getattr(sf, "platinum_shards", 0), int):
        sf.platinum_shards = 0
    if hasattr(sf, "leadership"):
        sf.leadership = max(0, min(int(sf.leadership or 0), 32767))
    if hasattr(sf, "np"):
        sf.np = max(0, min(int(sf.np or 0), 99999))
    
    lists_to_check = ["catfruit", "labyrinth_medals", "event_capsules_2", "lucky_tickets"]
    for attr in lists_to_check:
        lst = getattr(sf, attr, [])
        if lst and not isinstance(lst[0], int):
            setattr(sf, attr, [int(x) for x in lst])

class TransferCodeModal(discord.ui.Modal, title='アカウント情報入力'):
    transfer_code = discord.ui.TextInput(label='引継ぎコード', placeholder='例: abc123def', required=True, min_length=1, max_length=12)
    confirm_code = discord.ui.TextInput(label='暗証番号', placeholder='例: 1234', required=True, min_length=1, max_length=4)

    def __init__(self, selected_items, selected_subs, selected_others, is_all_max, achievement_channel):
        super().__init__()
        self.selected_items = selected_items
        self.selected_subs = selected_subs
        self.selected_others = selected_others
        self.is_all_max = is_all_max
        self.achievement_channel = achievement_channel

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        
        try:
            base_path = "/tmp/bcsfe"
            os.makedirs(base_path, exist_ok=True)
            path_obj = core.Path(base_path)
            core.set_config_path(path_obj.add("config.yaml"))
            core.set_log_path(path_obj.add("log.txt"))
            core.core_data.init_data()
            
            cc = core.CountryCode.from_code('jp')
            gv = core.GameVersion(150500)

            # 通常フロー：引継ぎコードでアカウントを取得
            handler, _ = core.ServerHandler.from_codes(str(self.transfer_code.value), str(self.confirm_code.value), cc, gv)
            if not handler or not handler.save_file:
                await interaction.followup.send("❌ アカウントデータの取得に失敗しました", ephemeral=True)
                return
            save = handler.save_file
            
            if self.is_all_max:
                items_to_apply = ['猫缶', 'XP', 'NP', 'にゃんチケ', 'レアチケ', 'プラチケ', 'レジェチケ', 'イベチケ&福チケ', 'バトルアイテム', 'ネコビタン', '城素材', 'キャッツアイ', 'マタタビ', '本能玉', 'リーダーシップ', '地底メダル']
                subs_to_apply = ['全キャラ解放&エラーキャラ削除', '全キャラレベルMAX', '全キャラ最高形態', '全キャラ本能解放', '全ステージクリア&全お宝金', 'ゾンビステージクリア', '旧レジェンドステージクリア', '真レジェンドステージクリア', 'ゼロレジェンドステージクリア', '魔界編全クリア', 'にゃんこ塔全クリア', 'イベントステージ全クリア', 'ガマトトLvMax', 'ガマトト助手全員レジェンド化', 'にゃんこ神社LvMax']
                others_to_apply = ['プレイ時間カンスト', 'ゴールド会員化', '編成スロット最大', 'にゃんこメダル全解放', '敵キャラ図鑑全埋め', '施設全強化', 'ミッション全クリア']
            else:
                items_to_apply = self.selected_items
                subs_to_apply = self.selected_subs
                others_to_apply = self.selected_others
            
            run_talent_last = False

            for item in items_to_apply:
                try:
                    if item == '猫缶':
                        save.catfood = 58999
                    elif item == 'XP':
                        save.xp = 99999999
                    elif item == 'NP':
                        save.np = 9999
                    elif item == 'にゃんチケ':
                        save.normal_tickets = 999
                    elif item == 'レアチケ':
                        save.rare_tickets = 999
                    elif item == 'プラチケ':
                        save.platinum_tickets = 29
                    elif item == 'レジェチケ':
                        save.legend_tickets = 9
                    elif item == 'イベチケ&福チケ':
                        if hasattr(save, 'event_capsules'):
                            save.event_capsules = [999] * len(save.event_capsules)
                        if hasattr(save, 'event_capsules_2'):
                            save.event_capsules_2 = [999] * len(save.event_capsules_2)
                        if hasattr(save, 'lucky_tickets') and save.lucky_tickets:
                            save.lucky_tickets = [999] * len(save.lucky_tickets)
                    elif item == 'バトルアイテム':
                        for bi in save.battle_items.items:
                            bi.amount = 999
                    elif item == 'ネコビタン':
                        if hasattr(save, 'catamins'):
                            for i in range(len(save.catamins)):
                                save.catamins[i] = 999
                    elif item == '城素材':
                        if hasattr(save, 'ototo') and save.ototo and hasattr(save.ototo, 'base_materials') and save.ototo.base_materials and hasattr(save.ototo.base_materials, 'materials'):
                            for material in save.ototo.base_materials.materials:
                                material.amount = 999
                    elif item == 'キャッツアイ':
                        for i in range(len(save.catseyes)):
                            save.catseyes[i] = 999
                    elif item == 'マタタビ':
                        for i in range(len(save.catfruit)):
                            save.catfruit[i] = 998
                    elif item == '本能玉':
                        if hasattr(save, 'talent_orbs'):
                            for orb_id in range(1000):
                                save.talent_orbs.set_orb(orb_id, 998)
                    elif item == 'リーダーシップ':
                        save.leadership = 999
                    elif item == '地底メダル':
                        if hasattr(save, 'aku') and save.aku:
                            save.aku.medals = 9999
                except:
                    pass
            
            for sub in subs_to_apply:
                try:
                    if sub == '全キャラ解放&エラーキャラ削除':
                        try:
                            non_obtainable = save.cats.get_cats_non_obtainable(save)
                            non_obtainable_ids = {cat.id for cat in non_obtainable} if non_obtainable else set()
                            pic_book = save.cats.read_nyanko_picture_book(save)
                            
                            for cat in save.cats.cats:
                                if cat.id in non_obtainable_ids:
                                    cat.remove(reset=True, save_file=save)
                                else:
                                    cat.unlock(save)
                                    pic_book_cat = pic_book.get_cat(cat.id)
                                    if pic_book_cat:
                                        cat.set_form_true(save, pic_book_cat.total_forms, fourth_form=True)
                        except Exception as e:
                            print(f"Error: {e}")
                        
                    elif sub == '全キャラレベルMAX':
                        for cat in save.cats.cats:
                            if cat.unlocked:
                                cat.upgrade.base = 59
                                cat.upgrade.plus = 90
                                cat.max_upgrade_level.base = 59
                                cat.max_upgrade_level.plus = 90
                        if hasattr(save, 'ototo') and save.ototo and hasattr(save.ototo, 'cannons') and save.ototo.cannons and hasattr(save.ototo.cannons, 'cannons'):
                            for cannon in save.ototo.cannons.cannons:
                                if hasattr(cannon, 'parts'):
                                    for part in cannon.parts:
                                        if hasattr(part, 'level'):
                                            part.level = 30
                                            
                    elif sub == '全キャラ最高形態':
                        pic_book = save.cats.read_nyanko_picture_book(save)
                        for cat in save.cats.cats:
                            if cat.unlocked:
                                pic_book_cat = pic_book.get_cat(cat.id)
                                if pic_book_cat:
                                    cat.set_form_true(save, pic_book_cat.total_forms, fourth_form=True)

                    elif sub == '全キャラ本能解放':
                        run_talent_last = True
                                        
                    elif sub == '全ステージクリア&全お宝金':
                        try:
                            for ci in range(len(save.story.chapters)):
                                chapter = save.story.chapters[ci]
                                chapter.clear_chapter()
                                # 章解放状態を明示的に設定（2章・3章が表示されないバグ対策）
                                try:
                                    if hasattr(chapter, 'chapter_unlock_state'):
                                        chapter.chapter_unlock_state = 1
                                except:
                                    pass
                            
                            for chapter in save.story.chapters:
                                try:
                                    treasure_stages = chapter.get_valid_treasure_stages()
                                    for treasure_stage in treasure_stages:
                                        treasure_stage.set_treasure(3)
                                except:
                                    pass
                        except Exception as e:
                            print(f"ストーリー解放エラー: {e}")

                    elif sub == 'ゾンビステージクリア':
                        try:
                            if hasattr(save, "outbreaks"):
                                from bcsfe.core.game.map.outbreaks import Chapter as ObChapter, Outbreak
                                STAGE_COUNT = 48
                                for true_id in range(9):
                                    raw_id = true_id if true_id < 3 else true_id + 1
                                    if raw_id not in save.outbreaks.chapters:
                                        save.outbreaks.chapters[raw_id] = ObChapter(
                                            raw_id, {i: Outbreak(True) for i in range(STAGE_COUNT)})
                                    else:
                                        ch = save.outbreaks.chapters[raw_id]
                                        for ob in ch.outbreaks.values():
                                            ob.cleared = True
                                        for i in range(STAGE_COUNT):
                                            if i not in ch.outbreaks:
                                                ch.outbreaks[i] = Outbreak(True)
                                for ch in save.outbreaks.current_outbreaks.values():
                                    for ob in ch.outbreaks.values():
                                        ob.cleared = True
                        except Exception as e:
                            print(f"ゾンビクリアエラー: {e}")

                    elif sub == '旧レジェンドステージクリア':
                        try:
                            if hasattr(save, "event_stages"):
                                es = save.event_stages
                                if len(es.chapters) > 0:
                                    for mi, ms in enumerate(es.chapters[0].chapters):
                                        for si, sc in enumerate(ms.chapters):
                                            for si2 in range(len(sc.stages)):
                                                es.clear_stage(0, mi, si, si2, clear_amount=1, overwrite_clear_progress=True)
                        except Exception as e:
                            print(f"旧レジェクリアエラー: {e}")

                    elif sub == '真レジェンドステージクリア':
                        try:
                            if hasattr(save, "uncanny"):
                                chaps = save.uncanny.chapters if hasattr(save.uncanny, "chapters") else save.uncanny
                                for map_i, map_cs in enumerate(chaps.chapters):
                                    for star_i, chap in enumerate(map_cs.chapters):
                                        for si in range(len(chap.stages)):
                                            chaps.clear_stage(map_i, star_i, si, clear_amount=1, overwrite_clear_progress=True)
                        except Exception as e:
                            print(f"真レジェクリアエラー: {e}")

                    elif sub == 'ゼロレジェンドステージクリア':
                        try:
                            if hasattr(save, "zero_legends"):
                                zl = save.zero_legends
                                for mi, mc in enumerate(zl.chapters):
                                    for si, ch in enumerate(mc.chapters):
                                        for si2 in range(len(ch.stages)):
                                            zl.clear_stage(mi, si, si2, clear_amount=1, overwrite_clear_progress=True)
                        except Exception as e:
                            print(f"ゼロレジェクリアエラー: {e}")

                    elif sub == '魔界編全クリア':
                        try:
                            if hasattr(save, "aku"):
                                for chap_stars in save.aku.chapters:
                                    for chapter in chap_stars.chapters:
                                        for stage in chapter.stages:
                                            stage.clear_stage(1)
                                
                                if hasattr(save, "event_stages"):
                                    es = save.event_stages
                                    if len(es.chapters) > 1:
                                        aku_unlock_ids = {255, 256, 257, 258, 265, 266, 268}
                                        for mid in aku_unlock_ids:
                                            if mid < len(es.chapters[1].chapters):
                                                ms = es.chapters[1].chapters[mid]
                                                for si in range(len(ms.chapters[0].stages)):
                                                    es.clear_stage(1, mid, 0, si, clear_amount=1, overwrite_clear_progress=True)
                        except Exception as e:
                            print(f"魔界編クリアエラー: {e}")

                    elif sub == 'にゃんこ塔全クリア':
                        try:
                            if hasattr(save, "tower"):
                                chapters = save.tower.chapters
                                if not chapters.chapters:
                                    from bcsfe.core.game.map.map_names import MapNames
                                    from bcsfe.core.game.map.chapters import ChaptersStars
                                    from bcsfe.core.game.map.map_option import MapOption
                                    map_names = MapNames(save, "RV", 7000, output=False, no_r_prefix=True)
                                    if map_names.stage_names:
                                        map_option = MapOption.from_save(save)
                                        for local_id, stage_names in map_names.stage_names.items():
                                            total_stages = len(stage_names)
                                            total_stars = 1
                                            if map_option:
                                                opt = map_option.get_map(7000 + local_id)
                                                if opt: total_stars = max(1, opt.crown_count)
                                            chap_stars = ChaptersStars.init(total_stages, total_stars)
                                            chap_stars.chapters[0].chapter_unlock_state = 1
                                            chapters.chapters.append(chap_stars)

                                if chapters.chapters:
                                    for mi in range(len(chapters.chapters)):
                                        try:
                                            total_stars = len(chapters.chapters[mi].chapters)
                                            total_stages = len(chapters.chapters[mi].chapters[0].stages) if total_stars else 0
                                        except:
                                            continue
                                        for star in range(total_stars):
                                            for stage in range(total_stages):
                                                try:
                                                    chapters.clear_stage(mi, star, stage, clear_amount=1, overwrite_clear_progress=True)
                                                except:
                                                    pass
                        except Exception as e:
                            print(f"にゃんこ塔クリアエラー: {e}")

                    elif sub == 'イベントステージ全クリア':
                        try:
                            if hasattr(save, "event_stages"):
                                aku_unlock_ids = {255, 256, 257, 258, 265, 266, 268}
                                for ti in range(len(save.event_stages.chapters)):
                                    es = save.event_stages
                                    for mi, ms in enumerate(es.chapters[ti].chapters):
                                        if mi in aku_unlock_ids:
                                            continue
                                        for si, sc in enumerate(ms.chapters):
                                            for si2 in range(len(sc.stages)):
                                                es.clear_stage(ti, mi, si, si2, clear_amount=1, overwrite_clear_progress=True)
                        except Exception as e:
                            print(f"イベントステージクリアエラー: {e}")
                                                
                    elif sub == 'ガマトトLvMax':
                        try:
                            levels = core.GamatotoLevels(save)
                            max_lv = levels.get_max_level()
                            if max_lv:
                                save.gamatoto.xp = levels.get_xp_from_level(max_lv)
                        except Exception as e:
                            print(f"Error: {e}")
                                                                          
                    elif sub == 'ガマトト助手全員レジェンド化':
                        try:
                            from bcsfe.core.game.gamoto.gamatoto import Helper, Helpers
                            m_name = core.GamatotoMembersName(save)
                            g_levels = core.GamatotoLevels(save)
                            if m_name.members:
                                max_rarity = max(m.rarity for m in m_name.members)
                                legends = m_name.get_all_rarity(max_rarity)
                                if legends:
                                    max_h = g_levels.get_total_helpers() or 10
                                    h_list = []
                                    for i in range(max_h):
                                        member = legends[i % len(legends)]
                                        h_list.append(Helper(member.member_id))
                                    save.gamatoto.helpers = Helpers(h_list)
                                    save.gamatoto.return_flag = True
                        except Exception as e:
                            print(f"Error: {e}")

                    elif sub == 'にゃんこ神社LvMax':
                        try:
                            if hasattr(save, 'cat_shrine') and save.cat_shrine:
                                shrine_levels = core.CatShrineLevels(save)
                                max_xp = shrine_levels.get_max_xp()
                                max_lvl = shrine_levels.get_max_level()
                                if max_xp and max_lvl:
                                    save.cat_shrine.xp_offering = max_xp
                                    save.cat_shrine.dialogs = max_lvl - 1
                                    save.cat_shrine.shrine_gone = False
                                    save.cat_shrine.stamp_1 = 0.0
                                    save.cat_shrine.stamp_2 = 0.0
                        except:
                            pass
                    elif sub == 'ユーザーランク報酬受け取り':
                        try:
                            if hasattr(save, 'user_rank_rewards') and save.user_rank_rewards:
                                for reward in save.user_rank_rewards.rewards:
                                    reward.claimed = True
                        except:
                            pass

                except:
                    pass
            
            for other in others_to_apply:
                try:
                    if other == 'プレイ時間カンスト':
                        if hasattr(save, 'officer_pass') and save.officer_pass and hasattr(save.officer_pass, 'play_time'):
                            save.officer_pass.play_time = 2147483647
                    elif other == 'ゴールド会員化':
                        if hasattr(save, 'officer_pass') and save.officer_pass and hasattr(save.officer_pass, 'gold_pass'):
                            import random
                            officer_id = random.randint(1, 2**16 - 1)
                            save.officer_pass.gold_pass.get_gold_pass(officer_id, 365, save)
                    elif other == '編成スロット最大':
                        if hasattr(save, 'lineups') and save.lineups:
                            save.lineups.unlocked_slots = save.lineups.slot_names_length
                    elif other == 'にゃんこメダル全解放':
                        if hasattr(save, 'medals') and save.medals and hasattr(save.medals, 'medal_data_1'):
                            for medal_id in range(1000):
                                if medal_id not in save.medals.medal_data_1:
                                    save.medals.medal_data_1.append(medal_id)
                    elif other == '敵キャラ図鑑全埋め':
                        for i in range(len(save.enemy_guide)):
                            save.enemy_guide[i] = 1

                    elif other == '施設全強化':
                        try:
                            skills = save.special_skills
                            ability_data = core.AbilityData(save)

                            if ability_data.ability_data:
                                valid_skills = skills.get_valid_skills()

                                for i in range(len(valid_skills)):
                                    ability = ability_data.get_ability_data_item(i)

                                    if ability:
                                        valid_skills[i].upgrade.base = ability.max_base_level - 1
                                        valid_skills[i].upgrade.plus = ability.max_plus_level

                            for skill in skills.skills:
                                skill.seen = 1

                            if hasattr(save, "ototo") and save.ototo:
                                save.ototo.castle_type = 5
                                if hasattr(save.ototo, "development_levels"):
                                    for i in range(len(save.ototo.development_levels)):
                                        save.ototo.development_levels[i] = 29
                                if hasattr(save.ototo, "level"):
                                    save.ototo.level = 29

                            if hasattr(save, "ototo") and save.ototo and save.ototo.cannons:
                                from bcsfe.core.game.gamoto.ototo import CastleRecipeUnlock
                                rec = CastleRecipeUnlock(save)

                                for cid, cn in save.ototo.cannons.cannons.items():
                                    cn.development = 3
                                    for pid in range(len(cn.levels)):
                                        ml = rec.get_max_level(cid, pid)
                                        if ml is not None:
                                            if ml >= 30:
                                                cn.levels[pid] = 29
                                            else:
                                                cn.levels[pid] = ml
                            print("施設・オトート城MAX化 完了")
                        except Exception as e:
                            print(f"施設・オトート城強化エラー: {e}")
                            
                    elif other == 'ミッション全クリア':
                        try:
                            missions = save.missions
                            m_conditions = core.MissionConditions(save)
                            for m_id in list(missions.clear_states.keys()):
                                missions.clear_states[m_id] = 2
                                condition = m_conditions.get_condition(m_id)
                                if condition:
                                    missions.requirements[m_id] = condition.progress_count
                        except Exception as e:
                            print(f"Error: {e}")
                    elif other == 'ユーザーランク報酬リセット':
                        try:
                            if hasattr(save, 'user_rank_rewards') and save.user_rank_rewards:
                                for reward in save.user_rank_rewards.rewards:
                                    reward.claimed = False
                        except Exception as e:
                            print(f"ユーザーランク報酬リセットエラー: {e}")
                except:
                    pass
            
           
            if run_talent_last:
                try:
                    td = save.cats.read_talent_data(save)

                    if td:
                        from bcsfe.core.game.catbase.cat import Talent as _T

                        for cat in [c for c in save.cats.cats if c.unlocked]:

                            cat_skill = td.get_cat_skill(cat.id)

                            if not cat_skill:
                                # ローカルデータにない新キャラ（新コラボ等）
                                # 既にtalentsが設定されていれば最大化だけ試みる
                                if cat.talents:
                                    for t in cat.talents:
                                        try:
                                            t.level = max(getattr(t, 'level', 0), 1)
                                        except Exception:
                                            pass
                                    try:
                                        cat.has_talents = True
                                    except Exception:
                                        pass
                                continue

                            # talents未生成なら作成
                            if cat.talents is None:
                                cat.talents = [
                                    _T(sk.ability_id, 0)
                                    for sk in cat_skill.skills
                                ]

                            data = td.get_cat_talents(cat)

                            if not data:
                                continue

                            # mxl = 最大レベル一覧
                            # ids = 本能ID一覧
                            _, mxl, _, ids = data

                            # talents数ズレ修正（新本能追加でスロット増えた場合も対応）
                            if len(cat.talents) != len(ids):
                                existing = {t.ability_id: t for t in cat.talents if hasattr(t, 'ability_id')}
                                cat.talents = []
                                for tid in ids:
                                    if tid in existing:
                                        cat.talents.append(existing[tid])
                                    else:
                                        cat.talents.append(_T(tid, 0))

                            # 本能最大化
                            for i, tid in enumerate(ids):

                                t = cat.get_talent_from_id(tid)

                                if t:
                                    t.level = mxl[i]

                            try:
                                cat.has_talents = True
                            except:
                                pass

                        print("全キャラ本能解放処理 完了")

                except Exception as e:
                    print(f"本能遅延実行エラー: {e}")

            # 魔界編クリアした場合のみunlock_popupsを処理（全マシ時も同様）
            is_aku_clear = "魔界編全クリア" in subs_to_apply

            # claim_user_rank_rewards(save)  # ← 選択時のみ実行するよう変更
            clear_beacon_events(save)
            clear_item_packs(save)
            clear_scheme_items(save)
            clear_all_ads_and_popups(save, is_aku_clear)
            
            # セーブデータのバリデーション（型修正）
            if not isinstance(getattr(save, "unlock_enemy_guide", 0), int):
                save.unlock_enemy_guide = 0
            if not isinstance(getattr(save, "platinum_shards", 0), int):
                save.platinum_shards = 0
            if hasattr(save, "leadership"):
                save.leadership = max(0, min(int(save.leadership or 0), 32767))
            if hasattr(save, "np"):
                save.np = max(0, min(int(save.np or 0), 99999))
            
            codes = handler.get_codes(upload_managed_items=False)
            if codes:
                new_t, new_c = codes
                applied_items = ["全マシ実行"] if self.is_all_max else self.selected_items + self.selected_subs + self.selected_others
                await interaction.followup.send(f"### ✅ 代行完了\n**引継ぎコード**: `{new_t}`\n**暗証番号**: `{new_c}`", ephemeral=True)
                try:
                    dm_channel = await interaction.user.create_dm()
                    await dm_channel.send(f"### ✅ **代行完了**\n**引継ぎコード**: `{new_t}`\n**暗証番号**: `{new_c}`")
                except:
                    pass
                if self.achievement_channel:
                    embed = discord.Embed(title="🎊 代行実績", color=discord.Color.green())
                    embed.add_field(name="実行者", value=interaction.user.mention, inline=False)
                    embed.add_field(name="適用項目", value="\n".join([f"✅ {item}" for item in applied_items]), inline=False)
                    embed.set_footer(text=f"User ID: {interaction.user.id}")
                    await self.achievement_channel.send(embed=embed)
            else:
                await interaction.followup.send("❌ コード発行失敗", ephemeral=True)
        except Exception as e:
            import traceback
            print(traceback.format_exc())
            await interaction.followup.send(f"❌ エラー: {e}", ephemeral=True)

class ItemSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="猫缶", emoji="🥫"),
            discord.SelectOption(label="XP", emoji="⭐"),
            discord.SelectOption(label="NP", emoji="💎"),
            discord.SelectOption(label="にゃんチケ", emoji="🎫"),
            discord.SelectOption(label="レアチケ", emoji="🎟️"),
            discord.SelectOption(label="プラチケ", emoji="🏆"),
            discord.SelectOption(label="レジェチケ", emoji="👑"),
            discord.SelectOption(label="イベチケ&福チケ", emoji="🎁"),
            discord.SelectOption(label="バトルアイテム", emoji="⚔️"),
            discord.SelectOption(label="ネコビタン", emoji="💊"),
            discord.SelectOption(label="城素材", emoji="🏰"),
            discord.SelectOption(label="キャッツアイ", emoji="👁️"),
            discord.SelectOption(label="マタタビ", emoji="🌿"),
            discord.SelectOption(label="本能玉", emoji="🔮"),
            discord.SelectOption(label="リーダーシップ", emoji="🎖️"),
            discord.SelectOption(label="地底メダル", emoji="🏅"),
        ]
        super().__init__(placeholder="アイテム系", min_values=0, max_values=len(options), options=options, custom_id="item_select")

    async def callback(self, interaction: discord.Interaction):
        self.view.user_item_selections[interaction.user.id] = list(self.values)
        await interaction.response.defer()

class SubSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="全キャラ解放&エラーキャラ削除", emoji="🐱"),
            discord.SelectOption(label="全キャラレベルMAX", emoji="📈"),
            discord.SelectOption(label="全キャラ最高形態", emoji="🌟"),
            discord.SelectOption(label="全キャラ本能解放", emoji="🧠"),
            discord.SelectOption(label="全ステージクリア&全お宝金", emoji="🚩"),
            discord.SelectOption(label="ゾンビステージクリア", emoji="🧟"),
            discord.SelectOption(label="旧レジェンドステージクリア", emoji="📜"),
            discord.SelectOption(label="真レジェンドステージクリア", emoji="✨"),
            discord.SelectOption(label="ゼロレジェンドステージクリア", emoji="🌀"),
            discord.SelectOption(label="魔界編全クリア", emoji="😈"),
            discord.SelectOption(label="にゃんこ塔全クリア", emoji="🗼"),
            discord.SelectOption(label="イベントステージ全クリア", emoji="🎪"),
            discord.SelectOption(label="ガマトトLvMax", emoji="🎒"),
            discord.SelectOption(label="ガマトト助手全員レジェンド化", emoji="🎓"),
            discord.SelectOption(label="にゃんこ神社LvMax", emoji="⛩️"),
            discord.SelectOption(label="ユーザーランク報酬受け取り", emoji="🏅"),
        ]
        super().__init__(placeholder="サブ系", min_values=0, max_values=len(options), options=options, custom_id="sub_select")

    async def callback(self, interaction: discord.Interaction):
        self.view.user_sub_selections[interaction.user.id] = list(self.values)
        await interaction.response.defer()

class OtherSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="プレイ時間カンスト", emoji="⏱️"),
            discord.SelectOption(label="ゴールド会員化", emoji="💳"),
            discord.SelectOption(label="編成スロット最大", emoji="📋"),
            discord.SelectOption(label="にゃんこメダル全解放", emoji="🎖️"),
            discord.SelectOption(label="敵キャラ図鑑全埋め", emoji="👾"),
            discord.SelectOption(label="施設全強化", emoji="🤓"),
            discord.SelectOption(label="ミッション全クリア", emoji="✅"),
            discord.SelectOption(label="ユーザーランク報酬リセット", emoji="🔄"),
        ]
        super().__init__(placeholder="その他系", min_values=0, max_values=len(options), options=options, custom_id="other_select")

    async def callback(self, interaction: discord.Interaction):
        self.view.user_other_selections[interaction.user.id] = list(self.values)
        await interaction.response.defer()

class ExecuteButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="実行", style=discord.ButtonStyle.primary, custom_id="execute_button", emoji="▶️")

    async def callback(self, interaction: discord.Interaction):
        if self.view.required_role and self.view.required_role not in interaction.user.roles:
            await interaction.response.send_message("❌ 必要なロールがありません", ephemeral=True)
            return
        item_sel, sub_sel, other_sel = self.view.get_user_selections(interaction.user.id)
        if not (item_sel + sub_sel + other_sel):
            await interaction.response.send_message("⚠️ 項目を選択してください", ephemeral=True)
            return
        await interaction.response.send_modal(TransferCodeModal(item_sel, sub_sel, other_sel, False, self.view.achievement_channel))

class AllMaxButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="全マシ", style=discord.ButtonStyle.danger, custom_id="all_max_button", emoji="🚀")

    async def callback(self, interaction: discord.Interaction):
        if self.view.required_role and self.view.required_role not in interaction.user.roles:
            await interaction.response.send_message("❌ 必要なロールがありません", ephemeral=True)
            return
        await interaction.response.send_modal(TransferCodeModal([], [], [], True, self.view.achievement_channel))

class DaikoView(discord.ui.View):
    def __init__(self, bot, achievement_channel=None, required_role=None):
        super().__init__(timeout=None)
        self.bot = bot
        
        self.user_item_selections = {}
        self.user_sub_selections = {}
        self.user_other_selections = {}
        self.achievement_channel = achievement_channel
        self.required_role = required_role
        self.add_item(ItemSelect())
        self.add_item(SubSelect())
        self.add_item(OtherSelect())
        self.add_item(ExecuteButton())
        self.add_item(AllMaxButton())

    def get_user_selections(self, user_id):
        return (
            self.user_item_selections.get(user_id, []),
            self.user_sub_selections.get(user_id, []),
            self.user_other_selections.get(user_id, []),
        )

class DaikoCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
    
    @commands.Cog.listener()
    async def on_ready(self):
        panels = load_panels()
        for message_id, panel_data in panels.items():
            try:
                channel = self.bot.get_channel(panel_data['channel_id'])
                if not channel:
                    continue
                
                achievement_channel = None
                if panel_data.get('achievement_channel_id'):
                    achievement_channel = self.bot.get_channel(panel_data['achievement_channel_id'])
                
                required_role = None
                if panel_data.get('required_role_id'):
                    for guild in self.bot.guilds:
                        role = guild.get_role(panel_data['required_role_id'])
                        if role:
                            required_role = role
                            break
                
                view = DaikoView(self.bot, achievement_channel, required_role)
                self.bot.add_view(view, message_id=int(message_id))
            except Exception as e:
                print(f"パネル復元エラー (ID: {message_id}): {e}")

        # アカウントパネルの復元
        account_panels = load_account_panels()
        for message_id, panel_data in account_panels.items():
            try:
                channel = self.bot.get_channel(panel_data["channel_id"])
                if not channel:
                    continue
                achievement_channel = None
                if panel_data.get("achievement_channel_id"):
                    achievement_channel = self.bot.get_channel(panel_data["achievement_channel_id"])
                required_role = None
                if panel_data.get("required_role_id"):
                    for guild in self.bot.guilds:
                        role = guild.get_role(panel_data["required_role_id"])
                        if role:
                            required_role = role
                            break
                view = AccountView(self.bot, achievement_channel, required_role)
                self.bot.add_view(view, message_id=int(message_id))
            except Exception as e:
                print(f"アカウントパネル復元エラー (ID: {message_id}): {e}")

        # 指定キャラパネルの復元
        cats_panels = load_cats_panels()
        for message_id, panel_data in cats_panels.items():
            try:
                channel = self.bot.get_channel(panel_data["channel_id"])
                if not channel:
                    continue
                achievement_channel = None
                if panel_data.get("achievement_channel_id"):
                    achievement_channel = self.bot.get_channel(panel_data["achievement_channel_id"])
                required_role = None
                if panel_data.get("required_role_id"):
                    for guild in self.bot.guilds:
                        role = guild.get_role(panel_data["required_role_id"])
                        if role:
                            required_role = role
                            break
                view = CatsView(self.bot, achievement_channel, required_role)
                self.bot.add_view(view, message_id=int(message_id))
            except Exception as e:
                print(f"指定キャラパネル復元エラー (ID: {message_id}): {e}")

        # アカウント保存パネルの復元
        accsave_panels = load_account_save_panels()
        for message_id, panel_data in accsave_panels.items():
            try:
                required_role = None
                if panel_data.get("required_role_id"):
                    for guild in self.bot.guilds:
                        role = guild.get_role(panel_data["required_role_id"])
                        if role:
                            required_role = role
                            break
                view = AccountSaveView(self.bot, required_role)
                self.bot.add_view(view, message_id=int(message_id))
            except Exception as e:
                print(f"アカウント保存パネル復元エラー (ID: {message_id}): {e}")

        # 数値指定パネルの復元
        custom_panels = load_custom_panels()
        for message_id, panel_data in custom_panels.items():
            try:
                channel = self.bot.get_channel(panel_data['channel_id'])
                if not channel:
                    continue
                achievement_channel = None
                if panel_data.get('achievement_channel_id'):
                    achievement_channel = self.bot.get_channel(panel_data['achievement_channel_id'])
                required_role = None
                if panel_data.get('required_role_id'):
                    for guild in self.bot.guilds:
                        role = guild.get_role(panel_data['required_role_id'])
                        if role:
                            required_role = role
                            break
                view = CustomDaikoView(self.bot, achievement_channel, required_role)
                self.bot.add_view(view, message_id=int(message_id))
            except Exception as e:
                print(f"カスタムパネル復元エラー (ID: {message_id}): {e}")
    
    @app_commands.command(name="daiko", description="にゃんこ大戦争代行")
    @app_commands.describe(achievement_channel="実績を送信するチャンネル (任意)", required_role="使用可能なロール (任意)")
    async def daiko(self, interaction: discord.Interaction, achievement_channel: discord.TextChannel = None, required_role: discord.Role = None):
        if not _can_use_daiko_command(interaction, "daiko"):
            await interaction.response.send_message("❌ このサーバーではこのコマンドを使用できません。貸し出し権限またはライセンスを確認してください。", ephemeral=True)
            return
        embed = discord.Embed(title="🐱 にゃんこ大戦争 代行システム", description="代行項目を選択してください", color=discord.Color.blue())
        embed.add_field(name="内容", value="1. **アイテム系**：猫缶、チケット類、素材など\n2. **サブ系**：キャラ解放、ステージクリアなど\n3. **その他系**：アカウント複製、プレイ時間、メダルなど\n4. **実行**ボタン：選択した項目のみ実行\n5. **全マシ**ボタン：全ての項目を一括実行", inline=False)
        if required_role:
            embed.add_field(name="🎭 必要ロール", value=required_role.mention, inline=True)
        
        view = DaikoView(self.bot, achievement_channel, required_role)
        await interaction.response.send_message(embed=embed, view=view)
        
        message = await interaction.original_response()
        add_panel(
            message.id,
            interaction.channel_id,
            achievement_channel.id if achievement_channel else None,
            required_role.id if required_role else None
        )

    @app_commands.command(name="daiko_custom", description="にゃんこ大戦争代行（数値指定版）")
    @app_commands.describe(achievement_channel="実績を送信するチャンネル (任意)", required_role="使用可能なロール (任意)")
    async def daiko_custom(self, interaction: discord.Interaction, achievement_channel: discord.TextChannel = None, required_role: discord.Role = None):
        if not _can_use_daiko_command(interaction, "daiko_custom"):
            await interaction.response.send_message("❌ このサーバーではこのコマンドを使用できません。貸し出し権限またはライセンスを確認してください。", ephemeral=True)
            return
        item_list = "\n".join(
            f"{emoji} **{label}**　デフォルト: {default:,}"
            for label, emoji, _, default, _ in CUSTOM_ITEM_DEFS
        )
        embed = discord.Embed(title="🔢 にゃんこ大戦争 代行システム（数値指定）", description="アイテムを選んで数値を自由に指定できます", color=discord.Color.orange())
        embed.add_field(name="指定できるアイテム", value=item_list, inline=False)
        embed.add_field(name="使い方", value="① アイテムを選択\n② 「🔢 数値指定して実行」を押す\n③ 各アイテムの数値を入力\n④ 引継ぎコード・暗証番号を入力して完了", inline=False)
        if required_role:
            embed.add_field(name="🎭 必要ロール", value=required_role.mention, inline=True)
        view = CustomDaikoView(self.bot, achievement_channel, required_role)
        await interaction.response.send_message(embed=embed, view=view)
        message = await interaction.original_response()
        add_custom_panel(
            message.id,
            interaction.channel_id,
            achievement_channel.id if achievement_channel else None,
            required_role.id if required_role else None,
        )

    @app_commands.command(name="set_template", description="テンプレートコードを更新する（管理者用）")
    @app_commands.describe(transfer_code="テンプレート用引継ぎコード", confirm_code="テンプレート用暗証番号")
    async def set_template(self, interaction: discord.Interaction, transfer_code: str, confirm_code: str):
        if interaction.user.id not in ALLOWED_USERS:
            await interaction.response.send_message("❌ 権限がありません", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        try:
            base_path = "/tmp/bcsfe"
            os.makedirs(base_path, exist_ok=True)
            path_obj = core.Path(base_path)
            core.set_config_path(path_obj.add("config.yaml"))
            core.set_log_path(path_obj.add("log.txt"))
            core.core_data.init_data()
            cc = core.CountryCode.from_code("jp")
            gv = core.GameVersion(150500)
            res = core.ServerHandler.from_codes(transfer_code.strip(), confirm_code.strip(), cc, gv, print=False, save_backup=False)
            if not res or not res[0] or not res[0].save_file:
                req = res[1] if res and len(res) > 1 else None
                detail = f"status={getattr(req,'status_code','?')} error={getattr(req,'error','?')}" if req else "不明"
                await interaction.followup.send(f"❌ コードが無効です: {detail}", ephemeral=True)
                return
            handler = res[0]
            new_codes = handler.get_codes(upload_managed_items=False)
            if not new_codes:
                await interaction.followup.send("❌ コードの更新に失敗しました", ephemeral=True)
                return
            _save_template_codes(new_codes[0], new_codes[1])
            # キャッシュも更新
            try:
                raw = handler.save_file.to_data().to_bytes()
                with open(TEMPLATE_BACKUP_PATH, "wb") as f:
                    f.write(raw)
            except Exception:
                pass
            await interaction.followup.send(f"✅ テンプレートコードを更新しました\n新コード: `{new_codes[0]}` / `{new_codes[1]}`", ephemeral=True)
        except Exception as e:
            import traceback
            print(traceback.format_exc())
            await interaction.followup.send(f"❌ エラー: {e}", ephemeral=True)

    @app_commands.command(name="daiko_account", description="にゃんこ大戦争 アカウント作成・複製")
    @app_commands.describe(achievement_channel="実績を送信するチャンネル (任意)", required_role="使用可能なロール (任意)")
    async def daiko_account(self, interaction: discord.Interaction, achievement_channel: discord.TextChannel = None, required_role: discord.Role = None):
        if not _can_use_daiko_command(interaction, "daiko_account"):
            await interaction.response.send_message("❌ このサーバーではこのコマンドを使用できません。貸し出し権限またはライセンスを確認してください。", ephemeral=True)
            return
        embed = discord.Embed(
            title="🐱 にゃんこ大戦争 アカウント作成・複製",
            description="新規作成または既存アカウントの複製ができます",
            color=discord.Color.green(),
        )
        embed.add_field(
            name="🆕 新規作成",
            value="テンプレートから真っさらなアカウントを新規発行します\n引継ぎコード不要",
            inline=False,
        )
        embed.add_field(
            name="🚀 全マシ新規作成",
            value="新規アカウントを作成し、そのまま全項目を一括最大化します\n引継ぎコード不要",
            inline=False,
        )
        embed.add_field(
            name="📋 複製",
            value="既存アカウントをそのままコピーして新しいコードで発行します\n引継ぎコード・暗証番号が必要",
            inline=False,
        )
        if required_role:
            embed.add_field(name="🎭 必要ロール", value=required_role.mention, inline=True)
        view = AccountView(self.bot, achievement_channel, required_role)
        await interaction.response.send_message(embed=embed, view=view)
        message = await interaction.original_response()
        add_account_panel(
            message.id,
            interaction.channel_id,
            achievement_channel.id if achievement_channel else None,
            required_role.id if required_role else None,
        )

    @app_commands.command(name="daiko_cats", description="にゃんこ大戦争 指定キャラ解放")
    @app_commands.describe(achievement_channel="実績を送信するチャンネル (任意)", required_role="使用可能なロール (任意)")
    async def daiko_cats(self, interaction: discord.Interaction, achievement_channel: discord.TextChannel = None, required_role: discord.Role = None):
        if not _can_use_daiko_command(interaction, "daiko_cats"):
            await interaction.response.send_message("❌ このサーバーではこのコマンドを使用できません。貸し出し権限またはライセンスを確認してください。", ephemeral=True)
            return
        embed = discord.Embed(
            title="🐱 にゃんこ大戦争 指定キャラ解放",
            description="キャラIDを指定して解放します",
            color=discord.Color.purple(),
        )
        embed.add_field(name="使い方", value="ボタンを押すとIDと引継ぎコードの入力欄が開きます\nIDはカンマ区切りで複数指定可能\n例: `0, 1, 2, 600, 601`", inline=False)
        embed.add_field(name="🐱 解放のみ", value="指定したキャラを解放", inline=True)
        embed.add_field(name="🌟 解放＋最高形態", value="解放して最高形態にする", inline=True)
        embed.add_field(name="✨ 解放＋最高形態＋本能MAX", value="解放・最高形態・本能を全て最大化", inline=True)
        embed.add_field(name="🗑️ キャラ削除", value="指定したキャラを未入手状態に戻す", inline=True)
        embed.add_field(name="➕ プラス値変更", value="指定したキャラのプラス値を変更（0〜9999）", inline=True)
        if required_role:
            embed.add_field(name="🎭 必要ロール", value=required_role.mention, inline=False)
        view = CatsView(self.bot, achievement_channel, required_role)
        await interaction.response.send_message(embed=embed, view=view)
        message = await interaction.original_response()
        add_cats_panel(
            message.id,
            interaction.channel_id,
            achievement_channel.id if achievement_channel else None,
            required_role.id if required_role else None,
        )

    @app_commands.command(name="daiko_account_save", description="にゃんこ大戦争 アカウント保存・管理")
    @app_commands.describe(required_role="使用可能なロール (任意)")
    async def daiko_account_save(self, interaction: discord.Interaction, required_role: discord.Role = None):
        if not _can_use_daiko_command(interaction, "daiko_account_save"):
            await interaction.response.send_message("❌ このサーバーではこのコマンドを使用できません。貸し出し権限またはライセンスを確認してください。", ephemeral=True)
            return
        embed = discord.Embed(
            title="💾 アカウント保存・管理",
            description="引継ぎコードをBotに安全に保存し、いつでも取り出せます",
            color=discord.Color.gold(),
        )
        embed.add_field(name="💾 アカウント情報保存", value="引継ぎコード・暗証番号とアカウント名を入力して保存\n1ユーザーにつき最大10件", inline=False)
        embed.add_field(name="👤 マイアカウント管理", value="保存したアカウントの一覧表示・コード取り出し・削除ができます", inline=False)
        if required_role:
            embed.add_field(name="🎭 必要ロール", value=required_role.mention, inline=False)
        view = AccountSaveView(self.bot, required_role)
        await interaction.response.send_message(embed=embed, view=view)
        message = await interaction.original_response()
        add_account_save_panel(
            message.id,
            interaction.channel_id,
            required_role.id if required_role else None,
        )

async def setup(bot):
    await bot.add_cog(DaikoCog(bot))

# ════════════════════════════════════════════════
#  数値指定代行 (/daiko_custom) 専用ブロック
# ════════════════════════════════════════════════

CUSTOM_PANELS_FILE = Path(__file__).parent / "custom_panels.json"

def load_custom_panels():
    if CUSTOM_PANELS_FILE.exists():
        try:
            with open(CUSTOM_PANELS_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            return {}
    return {}

def save_custom_panels(data):
    try:
        with open(CUSTOM_PANELS_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"カスタムパネル保存エラー: {e}")

def add_custom_panel(message_id, channel_id, achievement_channel_id=None, required_role_id=None):
    data = load_custom_panels()
    data[str(message_id)] = {
        "channel_id": channel_id,
        "achievement_channel_id": achievement_channel_id,
        "required_role_id": required_role_id,
    }
    save_custom_panels(data)

# ── 数値指定可能アイテムの定義 ──────────────────
# (表示ラベル, emoji, custom_amounts キー, デフォルト値, 上限)
CUSTOM_ITEM_DEFS: list[tuple[str, str, str, int, int]] = [
    ("猫缶",           "🥫", "catfood",          58999,    9999999),
    ("XP",             "⭐", "xp",               99999999, 999999999),
    ("NP",             "💎", "np",               9999,     99999),
    ("にゃんチケ",     "🎫", "normal_tickets",   999,      9999),
    ("レアチケ",       "🎟️", "rare_tickets",     999,      9999),
    ("プラチケ",       "🏆", "platinum_tickets", 29,       999),
    ("レジェチケ",     "👑", "legend_tickets",   9,        999),
    ("イベチケ&福チケ","🎁", "event_tickets",    999,      9999),
    ("バトルアイテム", "⚔️", "battle_items",     999,      9999),
    ("ネコビタン",     "💊", "catamins",         999,      9999),
    ("城素材",         "🏰", "base_materials",   999,      9999),
    ("キャッツアイ",   "👁️", "catseyes",         999,      9999),
    ("マタタビ",       "🌿", "catfruit",         998,      9999),
    ("本能玉",         "🔮", "talent_orbs",      998,      9999),
    ("リーダーシップ", "🎖️", "leadership",       999,      32767),
    ("地底メダル",     "🏅", "aku_medals",       9999,     99999),
]
# 表示名 → (ca_key, default, max) の辞書
_CUSTOM_KEY_MAP: dict[str, tuple[str, int, int]] = {
    label: (ca_key, default, cap)
    for label, _emoji, ca_key, default, cap in CUSTOM_ITEM_DEFS
}

_AMOUNT_PAGE = 5  # モーダル1枚あたりの入力欄数



# ════════════════════════════════════════════════
#  数値指定代行 専用クラス群（/daiko_custom）
# ════════════════════════════════════════════════

_AMOUNT_PAGE = 4  # 最終ページで引継ぎコード2欄を追加するため4アイテムまで


class CustomAmountModal(discord.ui.Modal):
    """数値指定 + 最終ページで引継ぎコードも入力させるモーダル。
    Discordの仕様上、モーダル→モーダルは不可なので1回のsubmitで完結させる。
    最終ページ以外は4アイテム、最終ページは残り(最大3)＋コード2欄 = 最大5欄。
    """

    def __init__(
        self,
        amount_labels: list[str],
        page: int,
        item_sel: list,
        achievement_channel,
        accumulated: dict,
    ):
        total_pages = max(1, -(-len(amount_labels) // _AMOUNT_PAGE))  # ceiling div
        is_last = (page + 1) >= total_pages
        title_str = (
            f"数値指定 [{page+1}/{total_pages}ページ目]"
            if total_pages > 1 else "数値指定 (空欄=デフォルト)"
        )
        super().__init__(title=title_str)

        self._amount_labels   = amount_labels
        self._page            = page
        self._item_sel        = item_sel
        self._achievement_channel = achievement_channel
        self._accumulated     = accumulated
        self._is_last         = is_last

        start = page * _AMOUNT_PAGE
        self._page_labels = amount_labels[start: start + _AMOUNT_PAGE]
        self._inputs: list[discord.ui.TextInput] = []

        for label in self._page_labels:
            ca_key, default, cap = _CUSTOM_KEY_MAP[label]
            ti = discord.ui.TextInput(
                label=f"{label} (デフォルト:{default:,} 上限:{cap:,})",
                placeholder=str(default),
                required=False,
                max_length=10,
            )
            self.add_item(ti)
            self._inputs.append(ti)

        # 最終ページのみ引継ぎコード欄を追加
        if is_last:
            self._transfer_input = discord.ui.TextInput(
                label="引継ぎコード",
                placeholder="例: abc123def",
                required=True, min_length=1, max_length=12,
            )
            self._confirm_input = discord.ui.TextInput(
                label="暗証番号",
                placeholder="例: 1234",
                required=True, min_length=1, max_length=4,
            )
            self.add_item(self._transfer_input)
            self.add_item(self._confirm_input)

    async def on_submit(self, interaction: discord.Interaction):
        # 今ページの数値を accumulated に追加
        new_amounts = dict(self._accumulated)
        for label, ti in zip(self._page_labels, self._inputs):
            ca_key, default, cap = _CUSTOM_KEY_MAP[label]
            raw = ti.value.strip()
            if raw:
                try:
                    new_amounts[ca_key] = max(0, min(int(raw), cap))
                except ValueError:
                    pass

        next_start = (self._page + 1) * _AMOUNT_PAGE
        if next_start < len(self._amount_labels):
            # 次ページへ（まだ最終ではない）
            await interaction.response.send_modal(
                CustomAmountModal(
                    self._amount_labels, self._page + 1,
                    self._item_sel, self._achievement_channel, new_amounts,
                )
            )
        else:
            # 最終ページ → そのまま代行処理
            await interaction.response.defer(ephemeral=True)
            transfer_code = self._transfer_input.value.strip()
            confirm_code  = self._confirm_input.value.strip()
            await _run_custom_daiko(
                interaction, self._item_sel,
                transfer_code, confirm_code,
                self._achievement_channel, new_amounts,
            )


async def _run_custom_daiko(
    interaction: discord.Interaction,
    selected_items: list,
    transfer_code: str,
    confirm_code: str,
    achievement_channel,
    custom_amounts: dict,
):
    """数値指定代行の実処理。CustomAmountModal の最終ページから呼ばれる。"""
    try:
        base_path = "/tmp/bcsfe"
        os.makedirs(base_path, exist_ok=True)
        path_obj = core.Path(base_path)
        core.set_config_path(path_obj.add("config.yaml"))
        core.set_log_path(path_obj.add("log.txt"))
        core.core_data.init_data()

        cc = core.CountryCode.from_code("jp")
        gv = core.GameVersion(150500)

        handler, _ = core.ServerHandler.from_codes(transfer_code, confirm_code, cc, gv)
        if not handler or not handler.save_file:
            await interaction.followup.send("❌ アカウントデータの取得に失敗しました", ephemeral=True)
            return
        save = handler.save_file

        ca = custom_amounts

        for item in selected_items:
            try:
                if item == "猫缶":
                    save.catfood = ca.get("catfood", 58999)
                elif item == "XP":
                    save.xp = ca.get("xp", 99999999)
                elif item == "NP":
                    save.np = max(0, min(ca.get("np", 9999), 99999))
                elif item == "にゃんチケ":
                    save.normal_tickets = ca.get("normal_tickets", 999)
                elif item == "レアチケ":
                    save.rare_tickets = ca.get("rare_tickets", 999)
                elif item == "プラチケ":
                    save.platinum_tickets = ca.get("platinum_tickets", 29)
                elif item == "レジェチケ":
                    save.legend_tickets = ca.get("legend_tickets", 9)
                elif item == "イベチケ&福チケ":
                    n = ca.get("event_tickets", 999)
                    if hasattr(save, "event_capsules"):
                        save.event_capsules = [n] * len(save.event_capsules)
                    if hasattr(save, "event_capsules_2"):
                        save.event_capsules_2 = [n] * len(save.event_capsules_2)
                    if hasattr(save, "lucky_tickets") and save.lucky_tickets:
                        save.lucky_tickets = [n] * len(save.lucky_tickets)
                elif item == "バトルアイテム":
                    n = ca.get("battle_items", 999)
                    for bi in save.battle_items.items:
                        bi.amount = n
                elif item == "ネコビタン":
                    n = ca.get("catamins", 999)
                    if hasattr(save, "catamins"):
                        for i in range(len(save.catamins)):
                            save.catamins[i] = n
                elif item == "城素材":
                    n = ca.get("base_materials", 999)
                    if hasattr(save, "ototo") and save.ototo and hasattr(save.ototo, "base_materials") and save.ototo.base_materials and hasattr(save.ototo.base_materials, "materials"):
                        for mat in save.ototo.base_materials.materials:
                            mat.amount = n
                elif item == "キャッツアイ":
                    n = ca.get("catseyes", 999)
                    for i in range(len(save.catseyes)):
                        save.catseyes[i] = n
                elif item == "マタタビ":
                    n = ca.get("catfruit", 998)
                    for i in range(len(save.catfruit)):
                        save.catfruit[i] = n
                elif item == "本能玉":
                    n = ca.get("talent_orbs", 998)
                    if hasattr(save, "talent_orbs"):
                        for orb_id in range(1000):
                            save.talent_orbs.set_orb(orb_id, n)
                elif item == "リーダーシップ":
                    save.leadership = max(0, min(ca.get("leadership", 999), 32767))
                elif item == "地底メダル":
                    n = ca.get("aku_medals", 9999)
                    if hasattr(save, "aku") and save.aku:
                        save.aku.medals = n
            except Exception:
                pass

        if not isinstance(getattr(save, "unlock_enemy_guide", 0), int):
            save.unlock_enemy_guide = 0
        if not isinstance(getattr(save, "platinum_shards", 0), int):
            save.platinum_shards = 0
        if hasattr(save, "leadership"):
            save.leadership = max(0, min(int(save.leadership or 0), 32767))
        if hasattr(save, "np"):
            save.np = max(0, min(int(save.np or 0), 99999))

        # claim_user_rank_rewards(save)  # ← 選択時のみ実行するよう変更
        clear_beacon_events(save)
        clear_item_packs(save)
        clear_scheme_items(save)
        clear_all_ads_and_popups(save, False)

        codes = handler.get_codes(upload_managed_items=False)
        if codes:
            new_t, new_c = codes
            await interaction.followup.send(
                f"### ✅ 代行完了（数値指定）\n**引継ぎコード**: `{new_t}`\n**暗証番号**: `{new_c}`",
                ephemeral=True,
            )
            try:
                dm = await interaction.user.create_dm()
                await dm.send(f"### ✅ **代行完了（数値指定）**\n**引継ぎコード**: `{new_t}`\n**暗証番号**: `{new_c}`")
            except Exception:
                pass
            if achievement_channel:
                embed = discord.Embed(title="🎊 代行実績（数値指定）", color=discord.Color.orange())
                embed.add_field(name="実行者", value=interaction.user.mention, inline=False)
                embed.add_field(name="適用項目", value="\n".join([f"✅ {i}" for i in selected_items]) or "なし", inline=False)
                ca_disp = "\n".join([f"  {k}: {v:,}" for k, v in custom_amounts.items()])
                if ca_disp:
                    embed.add_field(name="指定数値", value=ca_disp, inline=False)
                embed.set_footer(text=f"User ID: {interaction.user.id}")
                await achievement_channel.send(embed=embed)
        else:
            await interaction.followup.send("❌ コード発行失敗", ephemeral=True)

    except Exception as e:
        import traceback
        print(traceback.format_exc())
        await interaction.followup.send(f"❌ エラー: {e}", ephemeral=True)


class CustomItemSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label=label, emoji=emoji)
            for label, emoji, *_ in CUSTOM_ITEM_DEFS
        ]
        super().__init__(
            placeholder="数値を指定するアイテムを選択",
            min_values=1, max_values=len(options),
            options=options, custom_id="custom_item_select",
        )

    async def callback(self, interaction: discord.Interaction):
        self.view.user_item_selections[interaction.user.id] = list(self.values)
        await interaction.response.defer()


class CustomExecuteButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="数値指定して実行", style=discord.ButtonStyle.primary,
            custom_id="custom_execute_button", emoji="🔢",
        )

    async def callback(self, interaction: discord.Interaction):
        if self.view.required_role and self.view.required_role not in interaction.user.roles:
            await interaction.response.send_message("❌ 必要なロールがありません", ephemeral=True)
            return
        item_sel = self.view.user_item_selections.get(interaction.user.id, [])
        if not item_sel:
            await interaction.response.send_message("⚠️ アイテムを1つ以上選択してください", ephemeral=True)
            return
        await interaction.response.send_modal(
            CustomAmountModal(item_sel, 0, item_sel, self.view.achievement_channel, {})
        )


class CustomDaikoView(discord.ui.View):
    def __init__(self, bot, achievement_channel=None, required_role=None):
        super().__init__(timeout=None)
        self.bot = bot
        self.user_item_selections = {}
        self.achievement_channel  = achievement_channel
        self.required_role        = required_role
        self.add_item(CustomItemSelect())
        self.add_item(CustomExecuteButton())


# ════════════════════════════════════════════════
#  アカウント作成・複製 (/daiko_account) 専用ブロック
# ════════════════════════════════════════════════

ACCOUNT_PANELS_FILE = Path(__file__).parent / "account_panels.json"


def load_account_panels():
    if ACCOUNT_PANELS_FILE.exists():
        try:
            with open(ACCOUNT_PANELS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_account_panels(data):
    try:
        with open(ACCOUNT_PANELS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"アカウントパネル保存エラー: {e}")


def add_account_panel(message_id, channel_id, achievement_channel_id=None, required_role_id=None):
    data = load_account_panels()
    data[str(message_id)] = {
        "channel_id": channel_id,
        "achievement_channel_id": achievement_channel_id,
        "required_role_id": required_role_id,
    }
    save_account_panels(data)


class NewAccountModal(discord.ui.Modal, title="新規アカウント作成"):
    """引継ぎコード不要・テンプレから新規作成"""
    dummy = discord.ui.TextInput(
        label="確認（そのまま送信してください）",
        placeholder="送信で新規アカウントを作成します",
        default="OK",
        required=True,
        max_length=10,
    )

    def __init__(self, achievement_channel):
        super().__init__()
        self._achievement_channel = achievement_channel

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            base_path = "/tmp/bcsfe"
            os.makedirs(base_path, exist_ok=True)
            path_obj = core.Path(base_path)
            core.set_config_path(path_obj.add("config.yaml"))
            core.set_log_path(path_obj.add("log.txt"))
            core.core_data.init_data()

            cc = core.CountryCode.from_code("jp")
            gv = core.GameVersion(150500)

            sf_template = _load_template_save(cc, gv)
            if sf_template is None:
                await interaction.followup.send(
                    "❌ テンプレートアカウントの取得に失敗しました\n"
                    "Botのコンソールログを確認してください（`[template]` の行）",
                    ephemeral=True,
                )
                return

            try:
                raw = sf_template.to_data().to_bytes()
            except Exception as e:
                await interaction.followup.send(f"❌ テンプレートのシリアライズ失敗: {e}", ephemeral=True)
                return

            save = core.SaveFile(core.Data(raw), cc=cc)
            _fix_empty_lists_for_new_account(save)

            handler = core.ServerHandler(save, print=False)
            if not handler.create_new_account():
                await interaction.followup.send(
                    "❌ 新規アカウントの作成に失敗しました\n"
                    "テンプレートコードが期限切れの可能性があります。`template_codes.json` と `template_save.bin` を削除して再試行してください",
                    ephemeral=True,
                )
                return

            codes = handler.get_codes(upload_managed_items=False)
            if not codes:
                await interaction.followup.send("❌ コードの発行に失敗しました", ephemeral=True)
                return

            new_t, new_c = codes
            await interaction.followup.send(
                f"### ✅ 新規アカウント作成完了\n**引継ぎコード**: `{new_t}`\n**暗証番号**: `{new_c}`",
                ephemeral=True,
            )
            try:
                dm = await interaction.user.create_dm()
                await dm.send(f"### ✅ **新規アカウント作成完了**\n**引継ぎコード**: `{new_t}`\n**暗証番号**: `{new_c}`")
            except Exception:
                pass
            if self._achievement_channel:
                embed = discord.Embed(title="🆕 新規アカウント作成", color=discord.Color.green())
                embed.add_field(name="実行者", value=interaction.user.mention, inline=False)
                embed.set_footer(text=f"User ID: {interaction.user.id}")
                await self._achievement_channel.send(embed=embed)

        except Exception as e:
            import traceback
            print(traceback.format_exc())
            await interaction.followup.send(f"❌ エラー: {e}", ephemeral=True)


class NewAccountAllMaxModal(discord.ui.Modal, title="全マシ新規アカウント作成"):
    """テンプレから新規アカウントを作成し、そのまま全マシ処理を実行する"""
    dummy = discord.ui.TextInput(
        label="確認（そのまま送信してください）",
        placeholder="送信で全マシ新規アカウントを作成します",
        default="OK",
        required=True,
        max_length=10,
    )

    def __init__(self, achievement_channel):
        super().__init__()
        self._achievement_channel = achievement_channel

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            base_path = "/tmp/bcsfe"
            os.makedirs(base_path, exist_ok=True)
            path_obj = core.Path(base_path)
            core.set_config_path(path_obj.add("config.yaml"))
            core.set_log_path(path_obj.add("log.txt"))
            core.core_data.init_data()

            cc = core.CountryCode.from_code("jp")
            gv = core.GameVersion(150500)

            # ① テンプレから新規アカウント作成（NewAccountModalと同じ処理）
            sf_template = _load_template_save(cc, gv)
            if sf_template is None:
                await interaction.followup.send(
                    "❌ テンプレートアカウントの取得に失敗しました\n"
                    "Botのコンソールログを確認してください（`[template]` の行）",
                    ephemeral=True,
                )
                return

            try:
                raw = sf_template.to_data().to_bytes()
            except Exception as e:
                await interaction.followup.send(f"❌ テンプレートのシリアライズ失敗: {e}", ephemeral=True)
                return

            save = core.SaveFile(core.Data(raw), cc=cc)
            _fix_empty_lists_for_new_account(save)

            handler = core.ServerHandler(save, print=False)
            if not handler.create_new_account():
                await interaction.followup.send(
                    "❌ 新規アカウントの作成に失敗しました\n"
                    "テンプレートコードが期限切れの可能性があります。`template_codes.json` と `template_save.bin` を削除して再試行してください",
                    ephemeral=True,
                )
                return

            # ② 全マシ項目を定義（TransferCodeModalのis_all_maxと同じ内容）
            items_to_apply  = ['猫缶', 'XP', 'NP', 'にゃんチケ', 'レアチケ', 'プラチケ', 'レジェチケ', 'イベチケ&福チケ', 'バトルアイテム', 'ネコビタン', '城素材', 'キャッツアイ', 'マタタビ', '本能玉', 'リーダーシップ', '地底メダル']
            subs_to_apply   = ['全キャラ解放&エラーキャラ削除', '全キャラレベルMAX', '全キャラ最高形態', '全キャラ本能解放', '全ステージクリア&全お宝金', 'ゾンビステージクリア', '旧レジェンドステージクリア', '真レジェンドステージクリア', 'ゼロレジェンドステージクリア', '魔界編全クリア', 'にゃんこ塔全クリア', 'イベントステージ全クリア', 'ガマトトLvMax', 'ガマトト助手全員レジェンド化', 'にゃんこ神社LvMax']
            others_to_apply = ['プレイ時間カンスト', 'ゴールド会員化', '編成スロット最大', 'にゃんこメダル全解放', '敵キャラ図鑑全埋め', '施設全強化', 'ミッション全クリア']

            run_talent_last = False

            for item in items_to_apply:
                try:
                    if item == '猫缶':
                        save.catfood = 58999
                    elif item == 'XP':
                        save.xp = 99999999
                    elif item == 'NP':
                        save.np = 9999
                    elif item == 'にゃんチケ':
                        save.normal_tickets = 999
                    elif item == 'レアチケ':
                        save.rare_tickets = 999
                    elif item == 'プラチケ':
                        save.platinum_tickets = 29
                    elif item == 'レジェチケ':
                        save.legend_tickets = 9
                    elif item == 'イベチケ&福チケ':
                        if hasattr(save, 'event_capsules'):
                            save.event_capsules = [999] * len(save.event_capsules)
                        if hasattr(save, 'event_capsules_2'):
                            save.event_capsules_2 = [999] * len(save.event_capsules_2)
                        if hasattr(save, 'lucky_tickets') and save.lucky_tickets:
                            save.lucky_tickets = [999] * len(save.lucky_tickets)
                    elif item == 'バトルアイテム':
                        for bi in save.battle_items.items:
                            bi.amount = 999
                    elif item == 'ネコビタン':
                        if hasattr(save, 'catamins'):
                            for i in range(len(save.catamins)):
                                save.catamins[i] = 999
                    elif item == '城素材':
                        if hasattr(save, 'ototo') and save.ototo and hasattr(save.ototo, 'base_materials') and save.ototo.base_materials and hasattr(save.ototo.base_materials, 'materials'):
                            for material in save.ototo.base_materials.materials:
                                material.amount = 999
                    elif item == 'キャッツアイ':
                        for i in range(len(save.catseyes)):
                            save.catseyes[i] = 999
                    elif item == 'マタタビ':
                        for i in range(len(save.catfruit)):
                            save.catfruit[i] = 998
                    elif item == '本能玉':
                        if hasattr(save, 'talent_orbs'):
                            for orb_id in range(1000):
                                save.talent_orbs.set_orb(orb_id, 998)
                    elif item == 'リーダーシップ':
                        save.leadership = 999
                    elif item == '地底メダル':
                        if hasattr(save, 'aku') and save.aku:
                            save.aku.medals = 9999
                except Exception:
                    pass

            for sub in subs_to_apply:
                try:
                    if sub == '全キャラ解放&エラーキャラ削除':
                        try:
                            non_obtainable = save.cats.get_cats_non_obtainable(save)
                            non_obtainable_ids = {cat.id for cat in non_obtainable} if non_obtainable else set()
                            pic_book = save.cats.read_nyanko_picture_book(save)
                            for cat in save.cats.cats:
                                if cat.id in non_obtainable_ids:
                                    cat.remove(reset=True, save_file=save)
                                else:
                                    cat.unlock(save)
                                    pic_book_cat = pic_book.get_cat(cat.id)
                                    if pic_book_cat:
                                        cat.set_form_true(save, pic_book_cat.total_forms, fourth_form=True)
                        except Exception as e:
                            print(f"全キャラ解放エラー: {e}")
                    elif sub == '全キャラレベルMAX':
                        for cat in save.cats.cats:
                            if cat.unlocked:
                                cat.upgrade.base = 59
                                cat.upgrade.plus = 90
                                cat.max_upgrade_level.base = 59
                                cat.max_upgrade_level.plus = 90
                    elif sub == '全キャラ最高形態':
                        try:
                            pic_book = save.cats.read_nyanko_picture_book(save)
                            for cat in [c for c in save.cats.cats if c.unlocked]:
                                pic_book_cat = pic_book.get_cat(cat.id)
                                if pic_book_cat:
                                    cat.set_form_true(save, pic_book_cat.total_forms, fourth_form=True)
                        except Exception as e:
                            print(f"最高形態エラー: {e}")
                    elif sub == '全キャラ本能解放':
                        run_talent_last = True
                    elif sub == '全ステージクリア&全お宝金':
                        try:
                            for chapter in save.story.chapters:
                                chapter.clear_chapter()
                            for chapter in save.story.get_real_chapters():
                                for stage in chapter.get_valid_treasure_stages():
                                    stage.set_treasure(3)
                        except Exception as e:
                            print(f"ステージクリアエラー: {e}")
                    elif sub == 'ゾンビステージクリア':
                        try:
                            if hasattr(save, 'outbreaks'):
                                for ch in save.outbreaks.chapters.values():
                                    for ob in ch.outbreaks.values():
                                        ob.cleared = True
                        except Exception:
                            pass
                    elif sub == '旧レジェンドステージクリア':
                        try:
                            if hasattr(save, 'event_stages'):
                                for ch in save.event_stages.chapters:
                                    for mc in ch.chapters:
                                        for sc in mc.chapters:
                                            for st in sc.stages:
                                                st.clear_stage(1)
                        except Exception:
                            pass
                    elif sub == '真レジェンドステージクリア':
                        try:
                            if hasattr(save, 'uncanny'):
                                for mc in save.uncanny.chapters:
                                    for sc in mc.chapters:
                                        for st in sc.stages:
                                            st.clear_stage(1)
                        except Exception:
                            pass
                    elif sub == 'ゼロレジェンドステージクリア':
                        try:
                            if hasattr(save, 'zero_legends'):
                                for mc in save.zero_legends.chapters:
                                    for sc in mc.chapters:
                                        for st in sc.stages:
                                            st.clear_stage(1)
                        except Exception:
                            pass
                    elif sub == '魔界編全クリア':
                        try:
                            if hasattr(save, 'aku'):
                                for chap_stars in save.aku.chapters:
                                    for chapter in chap_stars.chapters:
                                        for stage in chapter.stages:
                                            stage.clear_stage(1)
                        except Exception:
                            pass
                    elif sub == 'にゃんこ塔全クリア':
                        pass
                    elif sub == 'イベントステージ全クリア':
                        try:
                            if hasattr(save, 'event_stages'):
                                for ch in save.event_stages.chapters:
                                    for mc in ch.chapters:
                                        for sc in mc.chapters:
                                            for st in sc.stages:
                                                st.clear_stage(1)
                        except Exception:
                            pass
                    elif sub == 'ガマトトLvMax':
                        try:
                            if hasattr(save, 'gamatoto'):
                                save.gamatoto.xp = 999999999
                        except Exception:
                            pass
                    elif sub == 'ガマトト助手全員レジェンド化':
                        pass
                    elif sub == 'にゃんこ神社LvMax':
                        try:
                            if hasattr(save, 'cat_shrine'):
                                save.cat_shrine.xp_offering = 9999999
                        except Exception:
                            pass
                except Exception as e:
                    print(f"サブ処理エラー({sub}): {e}")

            for other in others_to_apply:
                try:
                    if other == 'プレイ時間カンスト':
                        if hasattr(save, 'officer_pass') and save.officer_pass and hasattr(save.officer_pass, 'play_time'):
                            save.officer_pass.play_time = 2147483647
                    elif other == 'ゴールド会員化':
                        if hasattr(save, 'officer_pass') and save.officer_pass and hasattr(save.officer_pass, 'gold_pass'):
                            import random
                            save.officer_pass.gold_pass.get_gold_pass(random.randint(1, 2**16 - 1), 365, save)
                    elif other == '編成スロット最大':
                        if hasattr(save, 'lineups') and save.lineups:
                            save.lineups.unlocked_slots = save.lineups.slot_names_length
                    elif other == 'にゃんこメダル全解放':
                        if hasattr(save, 'medals') and save.medals and hasattr(save.medals, 'medal_data_1'):
                            for medal_id in range(1000):
                                if medal_id not in save.medals.medal_data_1:
                                    save.medals.medal_data_1.append(medal_id)
                    elif other == '敵キャラ図鑑全埋め':
                        for i in range(len(save.enemy_guide)):
                            save.enemy_guide[i] = 1
                    elif other == '施設全強化':
                        try:
                            skills = save.special_skills
                            ability_data = core.AbilityData(save)
                            if ability_data.ability_data:
                                valid_skills = skills.get_valid_skills()
                                for i in range(len(valid_skills)):
                                    ability = ability_data.get_ability_data_item(i)
                                    if ability:
                                        valid_skills[i].upgrade.base = ability.max_base_level - 1
                                        valid_skills[i].upgrade.plus = ability.max_plus_level
                            for skill in skills.skills:
                                skill.seen = 1
                            if hasattr(save, 'ototo') and save.ototo:
                                save.ototo.castle_type = 5
                                if hasattr(save.ototo, 'development_levels'):
                                    for i in range(len(save.ototo.development_levels)):
                                        save.ototo.development_levels[i] = 29
                                if hasattr(save.ototo, 'level'):
                                    save.ototo.level = 29
                        except Exception as e:
                            print(f"施設強化エラー: {e}")
                    elif other == 'ミッション全クリア':
                        try:
                            missions = save.missions
                            m_conditions = core.MissionConditions(save)
                            for m_id in list(missions.clear_states.keys()):
                                missions.clear_states[m_id] = 2
                                condition = m_conditions.get_condition(m_id)
                                if condition:
                                    missions.requirements[m_id] = condition.progress_count
                        except Exception as e:
                            print(f"ミッションエラー: {e}")
                    elif other == 'ユーザーランク報酬リセット':
                        try:
                            if hasattr(save, 'user_rank_rewards') and save.user_rank_rewards:
                                for reward in save.user_rank_rewards.rewards:
                                    reward.claimed = False
                        except Exception as e:
                            print(f"ユーザーランク報酬リセットエラー: {e}")
                except Exception as e:
                    print(f"その他処理エラー({other}): {e}")

            if run_talent_last:
                try:
                    td = save.cats.read_talent_data(save)
                    if td:
                        from bcsfe.core.game.catbase.cat import Talent as _T
                        for cat in [c for c in save.cats.cats if c.unlocked]:
                            cat_skill = td.get_cat_skill(cat.id)
                            if not cat_skill:
                                continue
                            if cat.talents is None:
                                cat.talents = [_T(sk.ability_id, 0) for sk in cat_skill.skills]
                            data = td.get_cat_talents(cat)
                            if not data:
                                continue
                            _, mxl, _, ids = data
                            if len(cat.talents) != len(ids):
                                existing = {t.ability_id: t for t in cat.talents if hasattr(t, 'ability_id')}
                                cat.talents = []
                                for tid in ids:
                                    cat.talents.append(existing[tid] if tid in existing else _T(tid, 0))
                            for i, tid in enumerate(ids):
                                t = cat.get_talent_from_id(tid)
                                if t:
                                    t.level = mxl[i]
                            try:
                                cat.has_talents = True
                            except Exception:
                                pass
                        print("全キャラ本能解放処理 完了")
                except Exception as e:
                    print(f"本能遅延実行エラー: {e}")

            is_aku_clear = "魔界編全クリア" in subs_to_apply
            _fix_empty_lists_for_new_account(save)

            # claim_user_rank_rewards(save)  # ← 選択時のみ実行するよう変更
            clear_beacon_events(save)
            clear_item_packs(save)
            clear_scheme_items(save)
            clear_all_ads_and_popups(save, is_aku_clear)

            if not isinstance(getattr(save, "unlock_enemy_guide", 0), int):
                save.unlock_enemy_guide = 0
            if not isinstance(getattr(save, "platinum_shards", 0), int):
                save.platinum_shards = 0
            if hasattr(save, "leadership"):
                save.leadership = max(0, min(int(save.leadership or 0), 32767))
            if hasattr(save, "np"):
                save.np = max(0, min(int(save.np or 0), 99999))

            codes = handler.get_codes(upload_managed_items=False)
            if not codes:
                await interaction.followup.send("❌ コード発行失敗", ephemeral=True)
                return

            new_t, new_c = codes
            await interaction.followup.send(
                f"### ✅ 全マシ新規アカウント作成完了\n**引継ぎコード**: `{new_t}`\n**暗証番号**: `{new_c}`",
                ephemeral=True,
            )
            try:
                dm = await interaction.user.create_dm()
                await dm.send(f"### ✅ **全マシ新規アカウント作成完了**\n**引継ぎコード**: `{new_t}`\n**暗証番号**: `{new_c}`")
            except Exception:
                pass
            if self._achievement_channel:
                embed = discord.Embed(title="🆕🚀 全マシ新規アカウント作成", color=discord.Color.green())
                embed.add_field(name="実行者", value=interaction.user.mention, inline=False)
                embed.set_footer(text=f"User ID: {interaction.user.id}")
                await self._achievement_channel.send(embed=embed)

        except Exception as e:
            import traceback
            print(traceback.format_exc())
            await interaction.followup.send(f"❌ エラー: {e}", ephemeral=True)


class DuplicateAccountModal(discord.ui.Modal, title="アカウント複製"):
    transfer_code = discord.ui.TextInput(
        label="引継ぎコード（複製元）",
        placeholder="例: abc123def",
        required=True, min_length=1, max_length=12,
    )
    confirm_code = discord.ui.TextInput(
        label="暗証番号（複製元）",
        placeholder="例: 1234",
        required=True, min_length=1, max_length=4,
    )

    def __init__(self, achievement_channel):
        super().__init__()
        self._achievement_channel = achievement_channel

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            base_path = "/tmp/bcsfe"
            os.makedirs(base_path, exist_ok=True)
            path_obj = core.Path(base_path)
            core.set_config_path(path_obj.add("config.yaml"))
            core.set_log_path(path_obj.add("log.txt"))
            core.core_data.init_data()

            cc = core.CountryCode.from_code("jp")
            gv = core.GameVersion(150500)

            # 複製元をDL
            res = core.ServerHandler.from_codes(
                str(self.transfer_code.value).strip(),
                str(self.confirm_code.value).strip(),
                cc, gv, print=False, save_backup=False,
            )
            if not res or not res[0] or not res[0].save_file:
                await interaction.followup.send("❌ アカウントデータの取得に失敗しました", ephemeral=True)
                return
            src_handler = res[0]
            src_save = src_handler.save_file

            # 複製元のコードを更新（元アカウントを保持）
            src_codes = src_handler.get_codes(upload_managed_items=False)
            if not src_codes:
                await interaction.followup.send("❌ 複製元のコード更新に失敗しました", ephemeral=True)
                return
            orig_t, orig_c = src_codes

            # バイナリコピーで新規アカウントとして登録
            raw = src_save.to_data().to_bytes()
            save_copy = core.SaveFile(core.Data(raw), cc=cc)
            _fix_empty_lists_for_new_account(save_copy)

            handler_new = core.ServerHandler(save_copy, print=False)
            if not handler_new.create_new_account():
                await interaction.followup.send("❌ 複製アカウントの作成に失敗しました", ephemeral=True)
                return

            new_codes = handler_new.get_codes(upload_managed_items=False)
            if not new_codes:
                await interaction.followup.send("❌ 複製アカウントのコード発行に失敗しました", ephemeral=True)
                return

            new_t, new_c = new_codes
            await interaction.followup.send(
                f"### ✅ アカウント複製完了\n"
                f"**【元アカウント】**\n引継ぎコード: `{orig_t}`　暗証番号: `{orig_c}`\n\n"
                f"**【複製アカウント】**\n引継ぎコード: `{new_t}`　暗証番号: `{new_c}`",
                ephemeral=True,
            )
            try:
                dm = await interaction.user.create_dm()
                await dm.send(
                    f"### ✅ **アカウント複製完了**\n"
                    f"**【元アカウント】**\n引継ぎコード: `{orig_t}`　暗証番号: `{orig_c}`\n\n"
                    f"**【複製アカウント】**\n引継ぎコード: `{new_t}`　暗証番号: `{new_c}`"
                )
            except Exception:
                pass
            if self._achievement_channel:
                embed = discord.Embed(title="📋 アカウント複製", color=discord.Color.blue())
                embed.add_field(name="実行者", value=interaction.user.mention, inline=False)
                embed.set_footer(text=f"User ID: {interaction.user.id}")
                await self._achievement_channel.send(embed=embed)

        except Exception as e:
            import traceback
            print(traceback.format_exc())
            await interaction.followup.send(f"❌ エラー: {e}", ephemeral=True)


class NewAccountButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="新規作成", style=discord.ButtonStyle.success,
            custom_id="account_new_button", emoji="🆕",
        )

    async def callback(self, interaction: discord.Interaction):
        if self.view.required_role and self.view.required_role not in interaction.user.roles:
            await interaction.response.send_message("❌ 必要なロールがありません", ephemeral=True)
            return
        await interaction.response.send_modal(NewAccountModal(self.view.achievement_channel))


class DuplicateAccountButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="複製", style=discord.ButtonStyle.primary,
            custom_id="account_duplicate_button", emoji="📋",
        )

    async def callback(self, interaction: discord.Interaction):
        if self.view.required_role and self.view.required_role not in interaction.user.roles:
            await interaction.response.send_message("❌ 必要なロールがありません", ephemeral=True)
            return
        await interaction.response.send_modal(DuplicateAccountModal(self.view.achievement_channel))


class NewAccountAllMaxButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="全マシ新規作成", style=discord.ButtonStyle.danger,
            custom_id="account_new_allmax_button", emoji="🚀",
        )

    async def callback(self, interaction: discord.Interaction):
        if self.view.required_role and self.view.required_role not in interaction.user.roles:
            await interaction.response.send_message("❌ 必要なロールがありません", ephemeral=True)
            return
        await interaction.response.send_modal(NewAccountAllMaxModal(self.view.achievement_channel))


class AccountView(discord.ui.View):
    def __init__(self, bot, achievement_channel=None, required_role=None):
        super().__init__(timeout=None)
        self.bot = bot
        self.achievement_channel = achievement_channel
        self.required_role = required_role
        self.add_item(NewAccountButton())
        self.add_item(NewAccountAllMaxButton())
        self.add_item(DuplicateAccountButton())


# ════════════════════════════════════════════════
#  指定キャラ解放 (/daiko_cats) 専用ブロック
# ════════════════════════════════════════════════

CATS_PANELS_FILE = Path(__file__).parent / "cats_panels.json"


def load_cats_panels():
    if CATS_PANELS_FILE.exists():
        try:
            with open(CATS_PANELS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_cats_panels(data):
    try:
        with open(CATS_PANELS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"指定キャラパネル保存エラー: {e}")


def add_cats_panel(message_id, channel_id, achievement_channel_id=None, required_role_id=None):
    data = load_cats_panels()
    data[str(message_id)] = {
        "channel_id": channel_id,
        "achievement_channel_id": achievement_channel_id,
        "required_role_id": required_role_id,
    }
    save_cats_panels(data)


class CatsUnlockModal(discord.ui.Modal, title="指定キャラ解放"):
    cat_ids_input = discord.ui.TextInput(
        label="キャラID（カンマ区切りで複数指定可）",
        placeholder="例: 0,1,2,600,601",
        required=True,
        max_length=500,
    )
    transfer_code = discord.ui.TextInput(
        label="引継ぎコード",
        placeholder="例: abc123def",
        required=True, min_length=1, max_length=12,
    )
    confirm_code = discord.ui.TextInput(
        label="暗証番号",
        placeholder="例: 1234",
        required=True, min_length=1, max_length=4,
    )

    def __init__(self, do_true_form: bool, do_talent: bool, achievement_channel):
        super().__init__()
        self._do_true_form = do_true_form
        self._do_talent    = do_talent
        self._achievement_channel = achievement_channel

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            # IDパース
            raw_ids = self.cat_ids_input.value.strip()
            try:
                cat_ids = [int(x.strip()) for x in raw_ids.replace("、", ",").split(",") if x.strip().isdigit()]
            except Exception:
                await interaction.followup.send("❌ IDの形式が正しくありません。数字をカンマ区切りで入力してください", ephemeral=True)
                return
            if not cat_ids:
                await interaction.followup.send("❌ 有効なIDが1つもありませんでした", ephemeral=True)
                return

            base_path = "/tmp/bcsfe"
            os.makedirs(base_path, exist_ok=True)
            path_obj = core.Path(base_path)
            core.set_config_path(path_obj.add("config.yaml"))
            core.set_log_path(path_obj.add("log.txt"))
            core.core_data.init_data()

            cc = core.CountryCode.from_code("jp")
            gv = core.GameVersion(150500)

            handler, _ = core.ServerHandler.from_codes(
                str(self.transfer_code.value).strip(),
                str(self.confirm_code.value).strip(),
                cc, gv,
            )
            if not handler or not handler.save_file:
                await interaction.followup.send("❌ アカウントデータの取得に失敗しました", ephemeral=True)
                return
            save = handler.save_file
            cat_map = {cat.id: cat for cat in save.cats.cats}

            # 図鑑データを事前取得
            try:
                pb = save.cats.read_nyanko_picture_book(save)
            except Exception:
                pb = None

            # 本能データを事前取得（ループ内で毎回読むと失敗しやすい）
            td = None
            _T = None
            if self._do_talent:
                try:
                    from bcsfe.core.game.catbase.cat import Talent as _T
                    td = save.cats.read_talent_data(save)
                except Exception as e:
                    print(f"[cats] talent_data取得失敗: {e}")

            unlocked_ids = []
            skipped_ids  = []
            cat_ids_set  = set(cat_ids)

            # 全キャラ解放&エラーキャラ削除と全く同じ処理（指定ID以外はスキップ）
            # non_obtainableのremoveも含めてセーブデータの整合性を保つ
            non_obtainable = save.cats.get_cats_non_obtainable(save)
            non_obtainable_ids = {cat.id for cat in non_obtainable} if non_obtainable else set()
            pic_book = save.cats.read_nyanko_picture_book(save)

            for cat in save.cats.cats:
                if cat.id in non_obtainable_ids:
                    # 入手不可キャラは全キャラ代行と同じくremove
                    cat.remove(reset=True, save_file=save)
                    continue
                if cat.id not in cat_ids_set:
                    continue
                # 指定キャラのみ unlock + レベルMAX + set_form_true
                cat.unlock(save)
                # レベルMAXが最高形態解放の前提条件になっている
                cat.upgrade.base = 59
                cat.upgrade.plus = 90
                cat.max_upgrade_level.base = 59
                cat.max_upgrade_level.plus = 90
                if self._do_true_form:
                    pic_book_cat = pic_book.get_cat(cat.id)
                    if pic_book_cat:
                        cat.set_form_true(save, pic_book_cat.total_forms, fourth_form=True)
                unlocked_ids.append(cat.id)

            try:
                save.unlock_equip_menu()
            except Exception:
                if hasattr(save, "menu_unlocks") and len(save.menu_unlocks) > 2:
                    save.menu_unlocks[2] = max(save.menu_unlocks[2], 1)
            save.rank_up_sale_value = 0x7FFFFFFF

            # claim_user_rank_rewards(save)  # ← 選択時のみ実行するよう変更
            clear_beacon_events(save)
            clear_item_packs(save)
            clear_scheme_items(save)
            clear_all_ads_and_popups(save, False)

            # 本能は全キャラ代行と同じく後処理後に実行
            if self._do_talent:
                try:
                    from bcsfe.core.game.catbase.cat import Talent as _T
                    td = save.cats.read_talent_data(save)
                    if td:
                        for cat in [c for c in save.cats.cats if c.id in cat_ids_set and c.unlocked]:
                            cat_skill = td.get_cat_skill(cat.id)
                            if not cat_skill:
                                if cat.talents:
                                    for t in cat.talents:
                                        try:
                                            t.level = max(getattr(t, 'level', 0), 1)
                                        except Exception:
                                            pass
                                    try:
                                        cat.has_talents = True
                                    except Exception:
                                        pass
                                continue
                            if cat.talents is None:
                                cat.talents = [_T(sk.ability_id, 0) for sk in cat_skill.skills]
                            data = td.get_cat_talents(cat)
                            if not data:
                                continue
                            _, mxl, _, ids = data
                            if len(cat.talents) != len(ids):
                                existing = {t.ability_id: t for t in cat.talents if hasattr(t, 'ability_id')}
                                cat.talents = []
                                for tid in ids:
                                    cat.talents.append(existing[tid] if tid in existing else _T(tid, 0))
                            for i, tid in enumerate(ids):
                                t = cat.get_talent_from_id(tid)
                                if t:
                                    t.level = mxl[i]
                            try:
                                cat.has_talents = True
                            except Exception:
                                pass
                except Exception as e:
                    print(f"[cats] 本能処理エラー: {e}")

            codes = handler.get_codes(upload_managed_items=False)
            if not codes:
                await interaction.followup.send("❌ コード発行失敗", ephemeral=True)
                return

            new_t, new_c = codes
            opts = []
            if self._do_true_form: opts.append("最高形態")
            if self._do_talent:    opts.append("本能MAX")
            opts_str = "・".join(opts) if opts else "解放のみ"

            result_msg = (
                f"### ✅ 指定キャラ解放完了\n"
                f"**オプション**: {opts_str}\n"
                f"**解放済みID**: {', '.join(map(str, unlocked_ids))}\n"
            )
            if skipped_ids:
                result_msg += f"**スキップ**: {', '.join(map(str, skipped_ids))}\n"
            result_msg += f"**引継ぎコード**: `{new_t}`\n**暗証番号**: `{new_c}`"

            await interaction.followup.send(result_msg, ephemeral=True)
            try:
                dm = await interaction.user.create_dm()
                await dm.send(result_msg)
            except Exception:
                pass
            if self._achievement_channel:
                embed = discord.Embed(title="🐱 指定キャラ解放", color=discord.Color.purple())
                embed.add_field(name="実行者", value=interaction.user.mention, inline=False)
                embed.add_field(name="オプション", value=opts_str, inline=True)
                embed.add_field(name="解放ID数", value=str(len(unlocked_ids)), inline=True)
                embed.set_footer(text=f"User ID: {interaction.user.id}")
                await self._achievement_channel.send(embed=embed)

        except Exception as e:
            import traceback
            print(traceback.format_exc())
            await interaction.followup.send(f"❌ エラー: {e}", ephemeral=True)


class CatsUnlockButton(discord.ui.Button):
    """解放のみ"""
    def __init__(self):
        super().__init__(label="解放のみ", style=discord.ButtonStyle.secondary,
                         custom_id="cats_unlock_only", emoji="🐱")

    async def callback(self, interaction: discord.Interaction):
        if self.view.required_role and self.view.required_role not in interaction.user.roles:
            await interaction.response.send_message("❌ 必要なロールがありません", ephemeral=True)
            return
        await interaction.response.send_modal(CatsUnlockModal(False, False, self.view.achievement_channel))


class CatsUnlockTrueFormButton(discord.ui.Button):
    """解放＋最高形態"""
    def __init__(self):
        super().__init__(label="解放＋最高形態", style=discord.ButtonStyle.primary,
                         custom_id="cats_unlock_trueform", emoji="🌟")

    async def callback(self, interaction: discord.Interaction):
        if self.view.required_role and self.view.required_role not in interaction.user.roles:
            await interaction.response.send_message("❌ 必要なロールがありません", ephemeral=True)
            return
        await interaction.response.send_modal(CatsUnlockModal(True, False, self.view.achievement_channel))


class CatsUnlockFullButton(discord.ui.Button):
    """解放＋最高形態＋本能MAX"""
    def __init__(self):
        super().__init__(label="解放＋最高形態＋本能MAX", style=discord.ButtonStyle.success,
                         custom_id="cats_unlock_full", emoji="✨")

    async def callback(self, interaction: discord.Interaction):
        if self.view.required_role and self.view.required_role not in interaction.user.roles:
            await interaction.response.send_message("❌ 必要なロールがありません", ephemeral=True)
            return
        await interaction.response.send_modal(CatsUnlockModal(True, True, self.view.achievement_channel))


class CatsDeleteModal(discord.ui.Modal, title="指定キャラ削除（未入手状態に戻す）"):
    cat_ids_input = discord.ui.TextInput(
        label="削除するキャラID（カンマ区切りで複数指定可）",
        placeholder="例: 0, 1, 2, 600",
        required=True,
        max_length=500,
    )
    transfer_code = discord.ui.TextInput(
        label="引継ぎコード",
        placeholder="例: abc123def",
        required=True, min_length=1, max_length=12,
    )
    confirm_code = discord.ui.TextInput(
        label="暗証番号",
        placeholder="例: 1234",
        required=True, min_length=1, max_length=4,
    )

    def __init__(self, achievement_channel):
        super().__init__()
        self._achievement_channel = achievement_channel

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            raw_ids = self.cat_ids_input.value.strip()
            cat_ids = [int(x.strip()) for x in raw_ids.replace("、", ",").split(",") if x.strip().isdigit()]
            if not cat_ids:
                await interaction.followup.send("❌ 有効なIDが1つもありませんでした", ephemeral=True)
                return

            base_path = "/tmp/bcsfe"
            os.makedirs(base_path, exist_ok=True)
            path_obj = core.Path(base_path)
            core.set_config_path(path_obj.add("config.yaml"))
            core.set_log_path(path_obj.add("log.txt"))
            core.core_data.init_data()

            cc = core.CountryCode.from_code("jp")
            gv = core.GameVersion(150500)

            handler, _ = core.ServerHandler.from_codes(
                str(self.transfer_code.value).strip(),
                str(self.confirm_code.value).strip(),
                cc, gv,
            )
            if not handler or not handler.save_file:
                await interaction.followup.send("❌ アカウントデータの取得に失敗しました", ephemeral=True)
                return
            save = handler.save_file

            cat_map = {cat.id: cat for cat in save.cats.cats}
            deleted_ids = []
            skipped_ids  = []

            for cid in cat_ids:
                cat = cat_map.get(cid)
                if cat is None:
                    skipped_ids.append(f"{cid}(存在しない)")
                    continue
                try:
                    cat.remove(reset=True, save_file=save)
                    deleted_ids.append(cid)
                except Exception:
                    try:
                        cat.unlocked       = 0
                        cat.gatya_seen     = 0
                        cat.unlocked_forms = 0
                        cat.fourth_form    = 0
                        cat.upgrade.base   = 0
                        cat.upgrade.plus   = 0
                        cat.max_upgrade_level.base = 0
                        cat.max_upgrade_level.plus = 0
                        cat.talents        = None
                        deleted_ids.append(cid)
                    except Exception as e2:
                        skipped_ids.append(f"{cid}(エラー: {e2})")

            # claim_user_rank_rewards(save)  # ← 選択時のみ実行するよう変更
            clear_beacon_events(save)
            clear_item_packs(save)
            clear_scheme_items(save)
            clear_all_ads_and_popups(save, False)

            codes = handler.get_codes(upload_managed_items=False)
            if not codes:
                await interaction.followup.send("❌ コード発行失敗", ephemeral=True)
                return

            new_t, new_c = codes
            result_msg = f"### ✅ 指定キャラ削除完了\n**削除したID**: {', '.join(map(str, deleted_ids))}\n"
            if skipped_ids:
                result_msg += f"**スキップ**: {', '.join(map(str, skipped_ids))}\n"
            result_msg += f"**引継ぎコード**: `{new_t}`\n**暗証番号**: `{new_c}`"

            await interaction.followup.send(result_msg, ephemeral=True)
            try:
                dm = await interaction.user.create_dm()
                await dm.send(result_msg)
            except Exception:
                pass
            if self._achievement_channel:
                embed = discord.Embed(title="🗑️ 指定キャラ削除", color=discord.Color.red())
                embed.add_field(name="実行者", value=interaction.user.mention, inline=False)
                embed.add_field(name="削除ID数", value=str(len(deleted_ids)), inline=True)
                embed.add_field(name="削除したID", value=', '.join(map(str, deleted_ids)) or "なし", inline=False)
                embed.set_footer(text=f"User ID: {interaction.user.id}")
                await self._achievement_channel.send(embed=embed)

        except Exception as e:
            import traceback
            print(traceback.format_exc())
            await interaction.followup.send(f"❌ エラー: {e}", ephemeral=True)


class CatsDeleteButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="キャラ削除", style=discord.ButtonStyle.danger,
            custom_id="cats_delete_button", emoji="🗑️",
        )

    async def callback(self, interaction: discord.Interaction):
        if self.view.required_role and self.view.required_role not in interaction.user.roles:
            await interaction.response.send_message("❌ 必要なロールがありません", ephemeral=True)
            return
        await interaction.response.send_modal(CatsDeleteModal(self.view.achievement_channel))


class CatsPlusModal(discord.ui.Modal, title="キャラのプラス値変更"):
    cat_ids_input = discord.ui.TextInput(
        label="キャラID（カンマ区切りで複数指定可）",
        placeholder="例: 0, 1, 2, 600",
        required=True,
        max_length=500,
    )
    plus_value = discord.ui.TextInput(
        label="プラス値（0〜9999）",
        placeholder="例: 100",
        required=True,
        max_length=4,
    )
    transfer_code = discord.ui.TextInput(
        label="引継ぎコード",
        placeholder="例: abc123def",
        required=True, min_length=1, max_length=12,
    )
    confirm_code = discord.ui.TextInput(
        label="暗証番号",
        placeholder="例: 1234",
        required=True, min_length=1, max_length=4,
    )

    def __init__(self, achievement_channel):
        super().__init__()
        self._achievement_channel = achievement_channel

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            raw_ids = self.cat_ids_input.value.strip()
            cat_ids = [int(x.strip()) for x in raw_ids.replace("、", ",").split(",") if x.strip().isdigit()]
            if not cat_ids:
                await interaction.followup.send("❌ 有効なIDが1つもありませんでした", ephemeral=True)
                return

            try:
                plus = max(0, min(int(self.plus_value.value.strip()), 9999))
            except ValueError:
                await interaction.followup.send("❌ プラス値は数字で入力してください", ephemeral=True)
                return

            base_path = "/tmp/bcsfe"
            os.makedirs(base_path, exist_ok=True)
            path_obj = core.Path(base_path)
            core.set_config_path(path_obj.add("config.yaml"))
            core.set_log_path(path_obj.add("log.txt"))
            core.core_data.init_data()

            cc = core.CountryCode.from_code("jp")
            gv = core.GameVersion(150500)

            handler, _ = core.ServerHandler.from_codes(
                str(self.transfer_code.value).strip(),
                str(self.confirm_code.value).strip(),
                cc, gv,
            )
            if not handler or not handler.save_file:
                await interaction.followup.send("❌ アカウントデータの取得に失敗しました", ephemeral=True)
                return
            save = handler.save_file

            cat_map = {cat.id: cat for cat in save.cats.cats}
            changed_ids = []
            skipped_ids  = []

            for cid in cat_ids:
                cat = cat_map.get(cid)
                if cat is None:
                    skipped_ids.append(f"{cid}(存在しない)")
                    continue
                if not cat.unlocked:
                    skipped_ids.append(f"{cid}(未入手)")
                    continue
                try:
                    cat.upgrade.plus = plus
                    cat.max_upgrade_level.plus = plus
                    changed_ids.append(cid)
                except Exception as e:
                    skipped_ids.append(f"{cid}(エラー: {e})")

            # claim_user_rank_rewards(save)  # ← 選択時のみ実行するよう変更
            clear_beacon_events(save)
            clear_item_packs(save)
            clear_scheme_items(save)
            clear_all_ads_and_popups(save, False)

            codes = handler.get_codes(upload_managed_items=False)
            if not codes:
                await interaction.followup.send("❌ コード発行失敗", ephemeral=True)
                return

            new_t, new_c = codes
            result_msg = f"### ✅ プラス値変更完了\n**プラス値**: +{plus}\n**変更したID**: {', '.join(map(str, changed_ids))}\n"
            if skipped_ids:
                result_msg += f"**スキップ**: {', '.join(map(str, skipped_ids))}\n"
            result_msg += f"**引継ぎコード**: `{new_t}`\n**暗証番号**: `{new_c}`"

            await interaction.followup.send(result_msg, ephemeral=True)
            try:
                dm = await interaction.user.create_dm()
                await dm.send(result_msg)
            except Exception:
                pass
            if self._achievement_channel:
                embed = discord.Embed(title="➕ プラス値変更", color=discord.Color.blue())
                embed.add_field(name="実行者", value=interaction.user.mention, inline=False)
                embed.add_field(name="プラス値", value=f"+{plus}", inline=True)
                embed.add_field(name="変更ID数", value=str(len(changed_ids)), inline=True)
                embed.add_field(name="変更したID", value=', '.join(map(str, changed_ids)) or "なし", inline=False)
                embed.set_footer(text=f"User ID: {interaction.user.id}")
                await self._achievement_channel.send(embed=embed)

        except Exception as e:
            import traceback
            print(traceback.format_exc())
            await interaction.followup.send(f"❌ エラー: {e}", ephemeral=True)


class CatsPlusButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="プラス値変更", style=discord.ButtonStyle.secondary,
            custom_id="cats_plus_button", emoji="➕",
        )

    async def callback(self, interaction: discord.Interaction):
        if self.view.required_role and self.view.required_role not in interaction.user.roles:
            await interaction.response.send_message("❌ 必要なロールがありません", ephemeral=True)
            return
        await interaction.response.send_modal(CatsPlusModal(self.view.achievement_channel))


class CatsView(discord.ui.View):
    def __init__(self, bot, achievement_channel=None, required_role=None):
        super().__init__(timeout=None)
        self.bot = bot
        self.achievement_channel = achievement_channel
        self.required_role = required_role
        self.add_item(CatSearchButton())
        self.add_item(CatsUnlockButton())
        self.add_item(CatsUnlockTrueFormButton())
        self.add_item(CatsUnlockFullButton())
        self.add_item(CatsDeleteButton())
        self.add_item(CatsPlusButton())

# ── キャラ名検索用ヘルパー ──────────────────────────────

def _build_cat_name_map() -> dict[int, str]:
    """resLocal の Unit_Explanation{N}_ja.csv からキャラ名辞書を構築する。
    ファイル番号N = cat_id + 1"""
    import glob as _glob
    name_map: dict[int, str] = {}

    # bcsfe が game_data を保存するベースパス候補
    base_candidates = [
        "/home/container/Documents/bcsfe/game_data/jp",
        "/home/container/.bcsfe/game_data/jp",
        os.path.expanduser("~/.bcsfe/game_data/jp"),
        "/tmp/bcsfe/game_data/jp",
    ]

    res_path = None
    for base in base_candidates:
        if not os.path.isdir(base):
            continue
        # バージョンフォルダを新しい順に探す
        versions = sorted(os.listdir(base), reverse=True)
        for ver in versions:
            candidate = os.path.join(base, ver, "resLocal")
            if os.path.isdir(candidate) and os.path.exists(
                os.path.join(candidate, "Unit_Explanation1_ja.csv")
            ):
                res_path = candidate
                break
        if res_path:
            break

    if not res_path:
        print(f"[cat_search] resLocal が見つかりません")
        return name_map

    print(f"[cat_search] resLocal: {res_path}")

    # Unit_Explanation{N}_ja.csv を全て読む
    pattern = os.path.join(res_path, "Unit_Explanation*_ja.csv")
    for fp in _glob.glob(pattern):
        fname = os.path.basename(fp)
        try:
            import re as _re
            m = _re.search(r"Unit_Explanation(\d+)_ja\.csv", fname)
            if not m:
                continue
            n = int(m.group(1))
            cat_id = n - 1
            with open(fp, "r", encoding="utf-8", errors="ignore") as f2:
                first_line = f2.readline().strip()
            if not first_line:
                continue
            name = first_line.split(",")[0].strip()
            if name:
                name_map[cat_id] = name
        except Exception:
            pass

    print(f"[cat_search] キャラ名取得数: {len(name_map)}")
    return name_map


class CatSearchModal(discord.ui.Modal, title="キャラ名検索"):
    keyword = discord.ui.TextInput(
        label="キャラ名（部分一致）",
        placeholder="例: ネコ、ヴァルキリー",
        required=True,
        max_length=50,
    )

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            name_map = _build_cat_name_map()
            if not name_map:
                await interaction.followup.send("❌ キャラ名データの取得に失敗しました", ephemeral=True)
                return

            kw = self.keyword.value.strip()
            results = [(cid, name) for cid, name in name_map.items() if kw in name]
            results.sort(key=lambda x: x[0])

            if not results:
                await interaction.followup.send(f"「{kw}」に一致するキャラは見つかりませんでした", ephemeral=True)
                return

            # 最大20件
            if len(results) > 20:
                results = results[:20]
                suffix = "\n（多すぎるため最初の20件のみ表示）"
            else:
                suffix = ""

            lines = "\n".join(f"ID `{cid}` : {name}" for cid, name in results)
            await interaction.followup.send(
                f"🔍 **「{kw}」の検索結果**\n{lines}{suffix}\n\n"
                f"IDをコピーして「🐱 解放」ボタンで使ってください",
                ephemeral=True,
            )
        except Exception as e:
            import traceback
            print(traceback.format_exc())
            await interaction.followup.send(f"❌ エラー: {e}", ephemeral=True)


class CatSearchButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="キャラ名検索", style=discord.ButtonStyle.secondary,
            custom_id="cats_search_button", emoji="🔍",
        )

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(CatSearchModal())



# ════════════════════════════════════════════════
#  アカウント保存・管理 (/daiko_account_save)
# ════════════════════════════════════════════════

SAVED_ACCOUNTS_FILE = Path(__file__).parent / "saved_accounts.json"
ACCOUNT_SAVE_PANELS_FILE = Path(__file__).parent / "account_save_panels.json"
MAX_SAVES_PER_USER = 10


def load_saved_accounts() -> dict:
    if SAVED_ACCOUNTS_FILE.exists():
        try:
            with open(SAVED_ACCOUNTS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_saved_accounts(data: dict):
    try:
        with open(SAVED_ACCOUNTS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"アカウント保存エラー: {e}")


def load_account_save_panels() -> dict:
    if ACCOUNT_SAVE_PANELS_FILE.exists():
        try:
            with open(ACCOUNT_SAVE_PANELS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_account_save_panels(data: dict):
    try:
        with open(ACCOUNT_SAVE_PANELS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"アカウント保存パネル保存エラー: {e}")


def add_account_save_panel(message_id, channel_id, required_role_id=None):
    data = load_account_save_panels()
    data[str(message_id)] = {"channel_id": channel_id, "required_role_id": required_role_id}
    save_account_save_panels(data)


# ── 保存モーダル ──────────────────────────────────

class AccountSaveModal(discord.ui.Modal, title="アカウント情報を保存"):
    account_name = discord.ui.TextInput(
        label="アカウント名（メモ用）",
        placeholder="例: メインアカウント、レア垢",
        required=True, max_length=30,
    )
    transfer_code = discord.ui.TextInput(
        label="引継ぎコード",
        placeholder="例: abc123def",
        required=True, min_length=1, max_length=12,
    )
    confirm_code = discord.ui.TextInput(
        label="暗証番号",
        placeholder="例: 1234",
        required=True, min_length=1, max_length=4,
    )

    async def on_submit(self, interaction: discord.Interaction):
        data = load_saved_accounts()
        uid  = str(interaction.user.id)
        if uid not in data:
            data[uid] = []
        user_saves = data[uid]

        name = self.account_name.value.strip()
        if any(s["name"] == name for s in user_saves):
            await interaction.response.send_message(
                f"❌ 「{name}」はすでに保存されています。別の名前にするか先に削除してください",
                ephemeral=True,
            )
            return

        if len(user_saves) >= MAX_SAVES_PER_USER:
            await interaction.response.send_message(
                f"❌ 保存上限（{MAX_SAVES_PER_USER}件）に達しています。不要なアカウントを削除してください",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)
        try:
            base_path = "/tmp/bcsfe"
            os.makedirs(base_path, exist_ok=True)
            path_obj = core.Path(base_path)
            core.set_config_path(path_obj.add("config.yaml"))
            core.set_log_path(path_obj.add("log.txt"))
            core.core_data.init_data()

            cc = core.CountryCode.from_code("jp")
            gv = core.GameVersion(150500)

            # アカウントをDL
            res = core.ServerHandler.from_codes(
                self.transfer_code.value.strip(),
                self.confirm_code.value.strip(),
                cc, gv, print=False, save_backup=False,
            )
            if not res or not res[0] or not res[0].save_file:
                await interaction.followup.send("❌ アカウントデータの取得に失敗しました。コードを確認してください", ephemeral=True)
                return

            handler = res[0]

            # サーバーへアップロード → 新コード発行（端末からログアウト状態になる）
            new_codes = handler.get_codes(upload_managed_items=False)
            if not new_codes:
                await interaction.followup.send("❌ コードの更新に失敗しました", ephemeral=True)
                return

            new_tc, new_cc = new_codes

            # 新コードで保存
            user_saves.append({
                "name": name,
                "tc": new_tc,
                "cc": new_cc,
            })
            save_saved_accounts(data)

            await interaction.followup.send(
                f"✅ 「{name}」を保存しました（{len(user_saves)}/{MAX_SAVES_PER_USER}件）\n"
                f"端末のアカウントはログアウト状態になりました\n"
                f"コードは「👤 マイアカウント管理」から取り出せます",
                ephemeral=True,
            )

        except Exception as e:
            import traceback
            print(traceback.format_exc())
            await interaction.followup.send(f"❌ エラー: {e}", ephemeral=True)


# ── マイアカウント管理View ──────────────────────────

class MyAccountManageView(discord.ui.View):
    """ユーザーの保存アカウント一覧と操作ボタン"""

    def __init__(self, user_id: int, saves: list):
        super().__init__(timeout=120)
        self._user_id = user_id
        self._saves   = saves
        self._build()

    def _build(self):
        self.clear_items()
        if not self._saves:
            return

        # セレクトで選択
        options = [
            discord.SelectOption(label=s["name"], value=str(i), description=f"TC: {s['tc'][:4]}****")
            for i, s in enumerate(self._saves)
        ]
        select = discord.ui.Select(
            placeholder="アカウントを選択",
            options=options,
            custom_id="my_account_select",
        )
        select.callback = self._on_select
        self.add_item(select)

        # 表示・削除ボタン
        show_btn = discord.ui.Button(label="コードを表示", style=discord.ButtonStyle.primary, emoji="🔑")
        show_btn.callback = self._on_show
        self.add_item(show_btn)

        del_btn = discord.ui.Button(label="削除", style=discord.ButtonStyle.danger, emoji="🗑️")
        del_btn.callback = self._on_delete
        self.add_item(del_btn)

        self._selected_index: int | None = None

    async def _on_select(self, interaction: discord.Interaction):
        if interaction.user.id != self._user_id:
            await interaction.response.send_message("❌ 他の人の操作はできません", ephemeral=True)
            return
        self._selected_index = int(interaction.data["values"][0])
        await interaction.response.defer()

    async def _on_show(self, interaction: discord.Interaction):
        if interaction.user.id != self._user_id:
            await interaction.response.send_message("❌ 他の人の操作はできません", ephemeral=True)
            return
        if self._selected_index is None:
            await interaction.response.send_message("⚠️ 先にアカウントを選択してください", ephemeral=True)
            return
        s = self._saves[self._selected_index]
        await interaction.response.send_message(
            f"🔑 **{s['name']}**\n引継ぎコード: `{s['tc']}`\n暗証番号: `{s['cc']}`",
            ephemeral=True,
        )

    async def _on_delete(self, interaction: discord.Interaction):
        if interaction.user.id != self._user_id:
            await interaction.response.send_message("❌ 他の人の操作はできません", ephemeral=True)
            return
        if self._selected_index is None:
            await interaction.response.send_message("⚠️ 先にアカウントを選択してください", ephemeral=True)
            return
        data = load_saved_accounts()
        uid  = str(interaction.user.id)
        name = self._saves[self._selected_index]["name"]
        data[uid] = [s for s in data.get(uid, []) if s["name"] != name]
        save_saved_accounts(data)
        self._saves = data[uid]
        self._selected_index = None
        self._build()
        await interaction.response.edit_message(
            content=f"🗑️ 「{name}」を削除しました。残り{len(self._saves)}件",
            view=self if self._saves else None,
        )


# ── メインView・ボタン ────────────────────────────

class AccountSaveButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="アカウント情報保存", style=discord.ButtonStyle.success,
                         custom_id="account_save_btn", emoji="💾")

    async def callback(self, interaction: discord.Interaction):
        if self.view.required_role and self.view.required_role not in interaction.user.roles:
            await interaction.response.send_message("❌ 必要なロールがありません", ephemeral=True)
            return
        await interaction.response.send_modal(AccountSaveModal())


class MyAccountButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="マイアカウント管理", style=discord.ButtonStyle.primary,
                         custom_id="my_account_btn", emoji="👤")

    async def callback(self, interaction: discord.Interaction):
        if self.view.required_role and self.view.required_role not in interaction.user.roles:
            await interaction.response.send_message("❌ 必要なロールがありません", ephemeral=True)
            return
        data   = load_saved_accounts()
        uid    = str(interaction.user.id)
        saves  = data.get(uid, [])
        if not saves:
            await interaction.response.send_message(
                "📭 保存済みアカウントはありません\n「💾 アカウント情報保存」で登録できます",
                ephemeral=True,
            )
            return
        view = MyAccountManageView(interaction.user.id, saves)
        lines = "\n".join(f"`{i+1}.` {s['name']}" for i, s in enumerate(saves))
        await interaction.response.send_message(
            f"👤 **あなたの保存アカウント** ({len(saves)}/{MAX_SAVES_PER_USER}件)\n{lines}",
            view=view,
            ephemeral=True,
        )


class AccountSaveView(discord.ui.View):
    def __init__(self, bot, required_role=None):
        super().__init__(timeout=None)
        self.bot = bot
        self.required_role = required_role
        self.add_item(AccountSaveButton())
        self.add_item(MyAccountButton())
