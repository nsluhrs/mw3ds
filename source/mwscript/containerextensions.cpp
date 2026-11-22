// Script functions: items (AddItem, RemoveItem, GetItemCount, Equip, Drop, soul gems)
#include "components/interpreter/interpreter.hpp"

#include <optional>
#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <unordered_set>

#include "mwworld/worldimp.hpp"
#include "mwmechanics/combathelpers.hpp"
#include "components/interpreter/interpreterhelpers.hpp"

std::optional<float> containerExtensions(Ctx& c, const Node& n, World& w, bool onPlayer, int ref, Ref* r)
{
	switch (n.func)
	{
	// Drop: the item leaves the inventory and lies at their feet (freed slaves' bracers). OpenMW: the whole amount
	// is dropped, what the actor doesn't carry is made; 0 or less does nothing, and only actors drop things
	case F_DROP:
	{
		std::string id = lower(argStr(c, n, 0));
		int want = (int)argNum(c, n, 1, 1);
		if (want <= 0 || !w.game.object(id) || (!onPlayer && r && r->actor < 0))
			return 0.0f;
		if (onPlayer || !r)
		{
			int have = std::min(want, w.itemCount(id));
			if (have > 0)
				w.removeItem(id, have);
			w.dropItemAt({ id, want, false }, w.player.feet, w.player.yaw, 50.0f);
			return 0.0f;
		}
		for (size_t k = 0; k < r->contents.size(); k++)
			if (r->contents[k].second == id)
			{
				int have = std::min(want, stockCount(r->contents[k].first));
				r->contents[k].first += r->contents[k].first < 0 ? have : -have;
				if (r->contents[k].first == 0)
					r->contents.erase(r->contents.begin() + k);
				break;
			}
		w.dropItemAt({ id, want, false }, r->pos, r->rot[2], 30.0f);
		return 0.0f;
	}
	// HasSoulGem creature: how many gems hold that creature's soul; RemoveSoulGem takes one
	case F_HASSOULGEM: case F_REMOVESOULGEM:
	{
		if (!onPlayer && r)
			return 0.0f;           // other actors' and containers' gems aren't kept with their souls
		std::string soul = lower(argStr(c, n, 0));
		int count = 0;
		for (size_t i = 0; i < w.inventory.size(); i++)
			if (w.inventory[i].soul == soul)
			{
				count += w.inventory[i].count;
				if (n.func == F_REMOVESOULGEM)
				{
					std::string id = w.inventory[i].id;
					if (--w.inventory[i].count <= 0)
						w.inventory.erase(w.inventory.begin() + i);
					w.itemLeft(id);
					return 1.0f;
				}
			}
		return (float)count;
	}

	// Items, as OpenMW's containerextensions.cpp: on the player, or on the container / actor named or running
	// the script; the gold coins of other sizes are gold_001; the messages (the game's own) only in conversation
	case F_GETITEMCOUNT:
	{
		std::string id = goldId(argStr(c, n, 0));
		if (onPlayer)
			return (float)w.itemCount(id);
		return r && (r->recType == REC_CONT || r->actor >= 0) ? (float)w.refItemCount(*r, id) : 0.0f;
	}
	case F_ADDITEM:
	case F_REMOVEITEM:
	{
		std::string id = goldId(argStr(c, n, 0));
		int count = (int)argNum(c, n, 1, 1.0f);
		if (count < 0)
			count = (u16)count;
		const Object* o = w.game.object(id);
		bool leveled = !o && w.game.leveled.count(id);
		if ((!o && !leveled) || count == 0 || (!onPlayer && (!r || (r->recType != REC_CONT && r->actor < 0))))
			return 0.0f;
		if (n.func == F_ADDITEM)
		{
			for (int k = 0; k < (leveled ? count : 1); k++)
			{
				std::string what = leveled ? w.game.pickLeveled(id, onPlayer ? w.stats.level : 1) : id;
				if (what.empty() || what == id && leveled)
					continue;
				if (onPlayer)
					w.addItem(what, leveled ? 1 : count);
				else
				{
					w.refAddItem(*r, what, leveled ? 1 : count);
					w.scriptGaveWear(*r, what);
				}
			}
			if (onPlayer && o && c.host.talking())
				c.host.notify(gmstFormat(w.game.gmst(count == 1 ? "sNotifyMessage60" : "sNotifyMessage61"), count, o->name));
			return 0.0f;
		}
		int removed = onPlayer ? w.removeItem(id, count) : w.refRemoveItem(*r, id, count);
		if (onPlayer && o && removed > 0 && c.host.talking())
			c.host.notify(gmstFormat(w.game.gmst(removed > 1 ? "sNotifyMessage63" : "sNotifyMessage62"), removed, o->name));
		return 0.0f;
	}
	case F_HASITEMEQUIPPED:
	{
		std::string id = lower(argStr(c, n, 0));
		if (!onPlayer && r)
		{
			// an NPC wears what it carries of armor, clothes and weapons
			const Object* o = r->actor >= 0 ? w.game.object(id) : nullptr;
			return o && (o->recType == REC_ARMO || o->recType == REC_CLOT || o->recType == REC_WEAP) && w.refItemCount(*r, id) > 0
				? 1.0f : 0.0f;
		}
		for (auto& it : w.inventory)
			if (it.id == id && it.equipped)
				return 1.0f;
		return 0.0f;
	}
	// GetArmorType slot: the MGOTG function TR scripts use -- the piece worn in an armor slot (0 helmet, 1 cuirass,
	// 2-3 pauldrons, 4 greaves, 5 boots, 6-7 gauntlets, 8 shield), its weight class as OpenMW returns it: 0 light,
	// 1 medium, 2 heavy, or -1 with nothing worn there. The slot is the record's own armor part number, so the class
	// is armorSkill's reading of its weight for that slot. An NPC wears what it carries (as HasItemEquipped reads
	// it), and its carried shield -- what it actually holds -- counts for slot 8
	case F_GETARMORTYPE:
	{
		int slot = (int)argNum(c, n, 0);
		if (slot < 0 || slot > 10)
			return -1.0f;   // (OpenMW throws "armor index out of range"; -1 just says nothing is worn)
		auto inSlot = [&](const Object* o) { return o && o->recType == REC_ARMO && o->subtype == slot; };
		const Object* armor = nullptr;
		if (onPlayer || !r)
		{
			for (auto& it : w.inventory)
				if (it.equipped && inSlot(w.game.object(it)))
				{
					armor = w.game.object(it);
					break;
				}
		}
		else if (r->actor >= 0)
		{
			for (auto& c2 : r->contents)
				if (inSlot(w.game.object(lower(c2.second))))
				{
					armor = w.game.object(lower(c2.second));
					break;
				}
			if (!armor && slot == 8)
			{
				const ActorDef& a = w.game.actors[r->actor];
				if (!a.shield.empty())
					armor = w.game.object(lower(a.shield));
			}
		}
		if (!armor)
			return -1.0f;
		// the weight class, as Session::armorSkill reads it for the skill training
		int sk = armor->weight <= kArmorSlotBaseWeight[slot] * w.game.gmstf("flightmaxmod", 0.6f) + 0.0005f
			? SKILL_LIGHT_ARMOR
			: armor->weight <= kArmorSlotBaseWeight[slot] * w.game.gmstf("fmedmaxmod", 0.9f) + 0.0005f
			? SKILL_MEDIUM_ARMOR : SKILL_HEAVY_ARMOR;
		return sk == SKILL_HEAVY_ARMOR ? 2.0f : sk == SKILL_MEDIUM_ARMOR ? 1.0f : 0.0f;
	}
	// GetWeaponType: what is in the right hand (OpenMW's OpGetWeaponType): a weapon's type (0 short blade .. 13 bolt),
	// -2 a lockpick, -3 a probe, -1 nothing (or anything else). A person holds the weapon it fights with while it has
	// it; a creature holds nothing
	case F_GETWEAPONTYPE:
	{
		const Object* o = nullptr;
		if (onPlayer || !r)
		{
			for (auto& it : w.inventory)
				if (it.equipped)
					if (const Object* x = w.game.object(it))
						if ((x->recType == REC_WEAP && x->subtype < WEAP_ARROW) || x->recType == REC_LOCK || x->recType == REC_PROB)
							o = x;
		}
		else if (r->actor >= 0 && !w.game.actors[r->actor].creature)
		{
			const std::string& wpn = w.game.actors[r->actor].weapon;
			if (!wpn.empty() && w.refItemCount(*r, wpn) > 0)
				o = w.game.object(wpn);
		}
		if (!o)
			return -1.0f;
		return o->recType == REC_LOCK ? -2.0f : o->recType == REC_PROB ? -3.0f : o->recType == REC_WEAP ? (float)o->subtype : -1.0f;
	}
	// Equip item: on the player it is used like from the inventory (added first when missing); an NPC wears
	// what it carries, so it is given the item when it has none
	case F_EQUIP:
		if (onPlayer || !r)
			c.host.equipPlayerItem(lower(argStr(c, n, 0)));
		else if (r->actor >= 0)
		{
			std::string id = lower(argStr(c, n, 0));
			if (w.game.object(id) && w.refItemCount(*r, id) == 0)
				w.refAddItem(*r, id, 1);
			w.scriptGaveWear(*r, id);
		}
		return 0.0f;
	default:
		return std::nullopt;
	}
}
