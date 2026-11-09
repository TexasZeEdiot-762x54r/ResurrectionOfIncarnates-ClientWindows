"""Convert local handler seconds to the native Dragon millisecond wire clock.

0x1401139b0 initializes the clock directly from response.time and then advances
it by GetTickCount milliseconds. Persisted journals remain in UTC seconds.
"""
from copy import deepcopy


def milliseconds(value):
    if (type(value) is not str or not value.isascii() or not value.isdigit()
            or value != str(int(value)) or len(value) > 12):
        raise ValueError('Invalid handler UTC seconds')
    return str(int(value) * 1000)


def reply(value):
    if not isinstance(value, dict) or 'result' not in value or 'time' not in value:
        return value
    out = deepcopy(value)
    out['time'] = milliseconds(out['time'])
    data = out.get('data')
    # Background selection compares raw envelope time; stages instead divide
    # that envelope by 1000. Do not globally convert every named date field.
    if isinstance(data, list):
        for row in data:
            if isinstance(row, dict) and {'sv_bgPath', 'sv_bgNum'} <= row.keys():
                for key in ('sv_beginTime', 'sv_endTime', 'sv_createTime'):
                    if key in row:
                        row[key] = milliseconds(row[key])
            elif isinstance(row, dict) and {'sv_charaId', 'sv_appearanceTime', 'sv_enableTime'} <= row.keys():
                # 0x1400a6ff7 compares appearanceTime to the raw response clock.
                for key in ('sv_appearanceTime', 'sv_enableTime'):
                    row[key] = milliseconds(row[key])
    # Boost profiles already contain absolute millisecond rows.
    if isinstance(data, dict):
        stacks = [data]
        for key in ('sv_compositeStacks', 'sv_finishedCompositeStacks'):
            if isinstance(data.get(key), list):
                stacks.extend(data[key])
        for stack in stacks:
            if isinstance(stack, dict) and {'sv_compositeStackNum', 'sv_mode', 'sv_beginTime', 'sv_endTime'} <= stack.keys():
                for key in ('sv_beginTime', 'sv_endTime'):
                    stack[key] = milliseconds(stack[key])
    return out
