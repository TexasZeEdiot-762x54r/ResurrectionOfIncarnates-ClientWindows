"""Native ranking contracts and the bounded shared revival win-ranking reader.

Readers: 0x1400c3bd0, 0x1400c4e70, 0x1400c5940, 0x1400c6ae0,
0x1400c7c50. Unsupported views retain typed empty replies. Never rank just
the local installation or substitute lifetime results for a requested period.
"""
import json
import math
import re
import urllib.request

PAGED = {'sv_filter', 'sv_start', 'sv_range', 'sv_period'}
FIELDS = {
    'getRankmatchRanking': PAGED | {'sv_type', 'sv_charaIdFilter'},
    'getCharaUsageRanking': {'sv_period'},
    'getCharaRanking': PAGED | {'sv_charaId'},
    'getCircleRanking': PAGED | {'sv_type'},
    'getTagRanking': PAGED,
    'getGoodRanking': PAGED,
}


def empty_reply(name, data):
    if name not in FIELDS or not isinstance(data, dict) or set(data) - FIELDS[name]:
        raise ValueError('Invalid ranking request fields')
    # Native builders write signed 32-bit integers. Retain enum values without
    # pretending their unrecovered filter/period semantics have been implemented.
    if any(type(v) is not int or not -(2**31) <= v < 2**31 for v in data.values()):
        raise ValueError('Invalid ranking request integer')
    result = {'ranking': []}
    if name != 'getCharaUsageRanking':
        result['sv_total'] = '0'
    if 'sv_type' in FIELDS[name]:
        result['sv_type'] = str(data.get('sv_type', 0))
    if name == 'getGoodRanking':
        result['sv_type'] = '4'
    if name == 'getCharaRanking':
        result['sv_charaId'] = str(data.get('sv_charaId', 0))
    return result


def strict_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate ranking field')
        result[key] = value
    return result


def supported(name, data):
    empty_reply(name, data)
    if name == 'getCharaUsageRanking':
        return data.get('sv_period') == 1
    # 0x1403ec570: controller mode 0 = Wins/Win %, mode 1 = Rank Point/Wins.
    # 0x1403eca20 maps those modes to types 1 and 2; visual page order differs.
    # Overall period index 0 maps to period 1.
    # The four filter labels map directly to 0=All, 1=Friends, 2=Circle, 3=Self.
    if (data.get('sv_period') != 1 or data.get('sv_filter') not in (0, 2, 3)
            or not 1 <= data.get('sv_start', 0) <= 10000
            or not 1 <= data.get('sv_range', 0) <= 100):
        return False
    if name == 'getRankmatchRanking':
        return (data.get('sv_type') == 1 and 0 <= data.get('sv_charaIdFilter', -1) <= 21
                or data.get('sv_type') in (2, 3) and data.get('sv_charaIdFilter') == 0)
    if name == 'getCircleRanking':
        # 0x1403d13c0 / 0x1403d1660: type 1 = Wins/Win %, type 2 = Rank Point/Wins.
        # Circle-tab filter semantics differ from the player filters above.
        return data.get('sv_type') in (1, 2) and data.get('sv_filter') == 0
    if name == 'getGoodRanking':
        return True
    return name == 'getCharaRanking' and 1 <= data.get('sv_charaId', 0) <= 21


def validate_response(name, data, result):
    expected = empty_reply(name, data)
    if not isinstance(result, dict) or set(result) != set(expected):
        raise ValueError('Invalid shared ranking response')
    for key in set(expected) - {'ranking', 'sv_total'}:
        if result[key] != expected[key]:
            raise ValueError('Ranking selector changed')
    def number(value, maximum, minimum=0):
        if (not isinstance(value, str) or not re.fullmatch(r'0|[1-9][0-9]*', value)
                or len(value) > 20 or not minimum <= int(value) <= maximum):
            raise ValueError('Invalid ranking number')
        return int(value)
    if name == 'getCharaUsageRanking':
        rows = result['ranking']
        if not isinstance(rows, list) or len(rows) > 21:
            raise ValueError('Invalid character usage rows')
        seen = set()
        for row in rows:
            if not isinstance(row, dict) or set(row) != {'sv_rank', 'sv_point', 'sv_subPoint', 'sv_charaId', 'sv_mostPartnerCharaId'}:
                raise ValueError('Invalid character usage row')
            number(row['sv_rank'], 21, 1)
            cid = number(row['sv_charaId'], 21, 1)
            number(row['sv_mostPartnerCharaId'], 21)
            if cid in seen:raise ValueError('Duplicate character usage row')
            seen.add(cid)
            for key in ('sv_point', 'sv_subPoint'):
                value = row[key]
                if (not isinstance(value, str) or len(value) > 20
                        or not re.fullmatch(r'[0-9]+(?:\.[0-9]+)?', value)
                        or not math.isfinite(float(value)) or not 0 <= float(value) <= 100):
                    raise ValueError('Invalid character usage percentage')
        return result
    total = number(result['sv_total'], 10000)
    rows = result['ranking']
    if not isinstance(rows, list) or len(rows) > min(data['sv_range'], total):
        raise ValueError('Invalid ranking page')
    circle_view = name == 'getCircleRanking'
    rank_points_view = name in ('getRankmatchRanking', 'getCircleRanking') and data.get('sv_type') == 2
    streak_view = name == 'getRankmatchRanking' and data.get('sv_type') == 3
    fields = {'sv_no', 'sv_rank', 'sv_point', 'sv_subPoint', 'sv_circleId', 'sv_circleName'}
    fields |= ({'sv_leaderUid', 'sv_leaderOnlineId', 'sv_leaderName'} if circle_view else
               {'sv_uid', 'sv_onlineId', 'sv_userName', 'sv_activeCharaId'})
    seen = set()
    for row in rows:
        if not isinstance(row, dict) or set(row) != fields:
            raise ValueError('Invalid ranking row')
        for key in ('sv_no', 'sv_rank'):
            number(row[key], 10000, 1)
        number(row['sv_point'], 2**31 - 1, 0 if circle_view and rank_points_view else 1)
        number(row['sv_circleId'], 2**63 - 1, 1 if circle_view else 0)
        uid = 'sv_leaderUid' if circle_view else 'sv_uid'
        online = 'sv_leaderOnlineId' if circle_view else 'sv_onlineId'
        identity = 'sv_circleId' if circle_view else 'sv_uid'
        number(row[uid], 2**64 - 1, 1)
        if not circle_view:
            number(row['sv_activeCharaId'], 21, 1)
        if row[online] != row[uid] or row[identity] in seen:
            raise ValueError('Invalid ranking row identity')
        seen.add(row[identity])
        rate = row['sv_subPoint']
        if streak_view:
            number(rate, 0)  # No secondary metric is displayed on the streak page.
        elif rank_points_view:
            number(rate, 2**31-1)  # This column is wins, not a percentage.
        elif (not isinstance(rate, str) or len(rate) > 20 or not re.fullmatch(r'[0-9]+(?:\.[0-9]+)?', rate)
                or not math.isfinite(float(rate)) or not 0 <= float(rate) <= 100):
            raise ValueError('Invalid ranking win percentage')
        for key in ('sv_leaderName' if circle_view else 'sv_userName', 'sv_circleName'):
            limit = 255 if key == 'sv_leaderName' else 127
            if (not isinstance(row[key], str) or len(row[key].encode('utf-8')) > limit
                    or any(ord(c) < 32 for c in row[key])):
                raise ValueError('Invalid ranking display name')
    return result


def request(url, token, name, data):
    if not supported(name, data):
        return empty_reply(name, data)
    body = json.dumps({'call': name, 'data': data}, separators=(',', ':')).encode()
    headers = {'Content-Type': 'application/json'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    req = urllib.request.Request(url, data=body, headers=headers, method='POST')
    try:
        with opener.open(req, timeout=3) as response:
            raw = response.read(128 * 1024 + 1)
        if len(raw) > 128 * 1024:
            raise ValueError('Ranking response too large')
        return validate_response(name, data, json.loads(raw, object_pairs_hook=strict_object))
    except (OSError, ValueError, TypeError, RecursionError):
        # A service not upgraded yet, or temporarily unavailable, must not end
        # the local game session. Never fall back to presenting local ranks.
        return empty_reply(name, data)
