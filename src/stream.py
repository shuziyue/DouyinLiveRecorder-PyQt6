# -*- encoding: utf-8 -*-

"""
Author: Hmily
GitHub: https://github.com/ihmily
Date: 2023-07-15 23:15:00
Update: 2025-02-06 02:28:00
Copyright (c) 2023-2025 by Hmily, All Rights Reserved.
Function: Get live stream data.
"""
import json
import re
from .utils import trace_error_decorator
from .http_clients.async_http import get_response_status

QUALITY_MAPPING = {"OD": 0, "BD": 0, "UHD": 1, "HD": 2, "SD": 3, "LD": 4}


def get_quality_index(quality) -> tuple:
    if not quality:
        return list(QUALITY_MAPPING.items())[0]

    quality_str = str(quality).upper()
    if quality_str.isdigit():
        quality_int = int(quality_str[0])
        quality_str = list(QUALITY_MAPPING.keys())[quality_int]
    return quality_str, QUALITY_MAPPING.get(quality_str, 0)


@trace_error_decorator
async def get_douyin_stream_url(json_data: dict, video_quality: str, proxy_addr: str) -> dict:
    anchor_name = json_data.get('anchor_name')

    result = {
        "anchor_name": anchor_name,
        "is_live": False,
    }

    status = json_data.get("status", 4)

    if status == 2:
        stream_url = json_data['stream_url']
        flv_url_dict = stream_url['flv_pull_url']
        flv_url_list: list = list(flv_url_dict.values())
        m3u8_url_dict = stream_url['hls_pull_url_map']
        m3u8_url_list: list = list(m3u8_url_dict.values())

        while len(flv_url_list) < 5:
            flv_url_list.append(flv_url_list[-1])
            m3u8_url_list.append(m3u8_url_list[-1])

        video_quality, quality_index = get_quality_index(video_quality)
        m3u8_url = m3u8_url_list[quality_index]
        flv_url = flv_url_list[quality_index]
        ok = await get_response_status(url=m3u8_url, proxy_addr=proxy_addr)
        if not ok:
            index = quality_index + 1 if quality_index < 4 else quality_index - 1
            m3u8_url = m3u8_url_list[index]
            flv_url = flv_url_list[index]
        result |= {
            'is_live': True,
            'title': json_data['title'],
            'quality': video_quality,
            'm3u8_url': m3u8_url,
            'flv_url': flv_url,
            'record_url': m3u8_url or flv_url,
        }
    return result