#!/usr/bin/env python3

from datetime import datetime
from pkg_resources import resource_filename

try:
    from ujson import dumps
    from flask import json as flask_json
    flask_json.dumps = lambda obj, **kwargs: dumps(obj, double_precision=6)
except ImportError:
    from json import dumps

from pogeo.webcache import SightingCache, SpawnCache
from flask import Flask, jsonify, make_response, Markup, render_template, request

from monocle import bounds, db, sanitized as conf
from monocle.names import DAMAGE, MOVES, POKEMON
from monocle.web_utils import *


app = Flask(__name__, template_folder=resource_filename('monocle', 'templates'), static_folder=resource_filename('monocle', 'static'))


def social_links():
    social_links = ''

    if conf.FB_PAGE_ID:
        social_links = '<a class="map_btn facebook-icon" target="_blank" href="https://www.facebook.com/' + conf.FB_PAGE_ID + '"></a>'
    if conf.TWITTER_SCREEN_NAME:
        social_links += '<a class="map_btn twitter-icon" target="_blank" href="https://www.twitter.com/' + conf.TWITTER_SCREEN_NAME + '"></a>'
    if conf.DISCORD_INVITE_ID:
        social_links += '<a class="map_btn discord-icon" target="_blank" href="https://discord.gg/' + conf.DISCORD_INVITE_ID + '"></a>'
    if conf.TELEGRAM_USERNAME:
        social_links += '<a class="map_btn telegram-icon" target="_blank" href="https://www.telegram.me/' + conf.TELEGRAM_USERNAME + '"></a>'

    return Markup(social_links)


def render_map():
    css_js = ''

    if conf.LOAD_CUSTOM_CSS_FILE:
        css_js = '<link rel="stylesheet" href="static/css/custom.css">'
    if conf.LOAD_CUSTOM_JS_FILE:
        css_js += '<script type="text/javascript" src="static/js/custom.js"></script>'

    js_vars = Markup(
        "_defaultSettings['FIXED_OPACITY'] = '{:d}'; "
        "_defaultSettings['SHOW_TIMER'] = '{:d}'; "
        "_defaultSettings['TRASH_IDS'] = [{}]; ".format(conf.FIXED_OPACITY, conf.SHOW_TIMER, ', '.join(str(p_id) for p_id in conf.TRASH_IDS)))

    template = app.jinja_env.get_template('custom.html' if conf.LOAD_CUSTOM_HTML_FILE else 'newmap.html')
    return template.render(
        area_name=conf.AREA_NAME,
        map_center=bounds.center,
        map_provider_url=conf.MAP_PROVIDER_URL,
        map_provider_attribution=conf.MAP_PROVIDER_ATTRIBUTION,
        social_links=social_links(),
        init_js_vars=js_vars,
        extra_css_js=Markup(css_js)
    )


def render_worker_map():
    template = app.jinja_env.get_template('workersmap.html')
    return template.render(
        area_name=conf.AREA_NAME,
        map_center=bounds.center,
        map_provider_url=conf.MAP_PROVIDER_URL,
        map_provider_attribution=conf.MAP_PROVIDER_ATTRIBUTION,
        social_links=social_links()
    )


@app.route('/')
def fullmap(map_html=render_map()):
    return map_html


@app.route('/data')
def pokemon_data(
        cache=SightingCache(
            conf.TRASH_IDS, POKEMON, MOVES, DAMAGE, conf.MAP_FILTER_IDS,
            db.Sighting, db.Session, conf.SPAWN_ID_INT
        ),
        _resp=make_response):
    try:
        compress = 'gzip' in request.headers['Accept-Encoding'].lower()
    except KeyError:
        compress = False
    try:
        last_id = int(request.args['last_id'])
    except KeyError:
        last_id = 0
    response = _resp(cache.get_json(last_id, compress))
    response.mimetype = 'application/json'
    if compress:
        response.headers['Content-Encoding'] = 'gzip'
    return response


@app.route('/gym_data')
def gym_data():
    return jsonify(get_gym_markers())


@app.route('/spawnpoints')
def spawn_points(
        cache=SpawnCache(conf.SPAWN_ID_INT, db.Spawnpoint, db.Session),
        _resp=make_response):
    compress = 'gzip' in request.headers.get('Accept-Encoding', '').lower()
    response = _resp(cache.get_json(compress))
    response.mimetype = 'application/json'
    if compress:
        response.headers['Content-Encoding'] = 'gzip'
    return response


@app.route('/pokestops')
def get_pokestops(_scope=db.session_scope, _stop=db.Pokestop):
    with _scope() as session:
        pokestops = session.query(_stop.external_id, _stop.lat, _stop.lon)
        return jsonify(pokestops.all())


@app.route('/scan_coords')
def scan_coords(_coords=bounds.json, _resp=make_response):
    response = _resp(_coords)
    response.mimetype = 'application/json'
    return response


if conf.MAP_WORKERS:
    workers = Workers()

    @app.route('/workers_data')
    def workers_data():
        return jsonify(get_worker_markers(workers))


    @app.route('/workers')
    def workers_map(map_html=render_worker_map()):
        return map_html



@app.route('/report')
def report_main(area_name=conf.AREA_NAME,
                names=POKEMON,
                key=conf.GOOGLE_MAPS_KEY if conf.REPORT_MAPS else None):
    with db.session_scope() as session:
        counts = db.get_sightings_per_pokemon(session)

        count = sum(counts.values())
        counts_tuple = tuple(counts.items())
        nonexistent = [(x, names[x]) for x in range(1, 252) if x not in counts]
        del counts

        top_pokemon = list(counts_tuple[-30:])
        top_pokemon.reverse()
        bottom_pokemon = counts_tuple[:30]
        rare_pokemon = [r for r in counts_tuple if r[0] in conf.RARE_IDS]
        if rare_pokemon:
            rare_sightings = db.get_all_sightings(
                session, [r[0] for r in rare_pokemon]
            )
        else:
            rare_sightings = []
        js_data = {
            'charts_data': {
                'punchcard': db.get_punch_card(session),
                'top30': [(names[r[0]], r[1]) for r in top_pokemon],
                'bottom30': [
                    (names[r[0]], r[1]) for r in bottom_pokemon
                ],
                'rare': [
                    (names[r[0]], r[1]) for r in rare_pokemon
                ],
            },
            'maps_data': {
                'rare': [sighting_to_report_marker(s) for s in rare_sightings],
            },
            'map_center': bounds.center,
            'zoom': 13,
        }
    icons = {
        'top30': [(r[0], names[r[0]]) for r in top_pokemon],
        'bottom30': [(r[0], names[r[0]]) for r in bottom_pokemon],
        'rare': [(r[0], names[r[0]]) for r in rare_pokemon],
        'nonexistent': nonexistent
    }
    session_stats = db.get_session_stats(session)
    return render_template(
        'report.html',
        current_date=datetime.now(),
        area_name=area_name,
        area_size=bounds.area,
        total_spawn_count=count,
        spawns_per_hour=count // session_stats['length_hours'],
        session_start=session_stats['start'],
        session_end=session_stats['end'],
        session_length_hours=session_stats['length_hours'],
        js_data=js_data,
        icons=icons,
        google_maps_key=key,
    )


@app.route('/report/<int:pokemon_id>')
def report_single(pokemon_id,
                  area_name=conf.AREA_NAME,
                  key=conf.GOOGLE_MAPS_KEY if conf.REPORT_MAPS else None):
    with db.session_scope() as session:
        session_stats = db.get_session_stats(session)
        js_data = {
            'charts_data': {
                'hours': db.get_spawns_per_hour(session, pokemon_id),
            },
            'map_center': bounds.center,
            'zoom': 13,
        }
        return render_template(
            'report_single.html',
            current_date=datetime.now(),
            area_name=area_name,
            area_size=bounds.area,
            pokemon_id=pokemon_id,
            pokemon_name=POKEMON[pokemon_id],
            total_spawn_count=db.get_total_spawns_count(session, pokemon_id),
            session_start=session_stats['start'],
            session_end=session_stats['end'],
            session_length_hours=int(session_stats['length_hours']),
            google_maps_key=key,
            js_data=js_data,
        )


@app.route('/report/heatmap')
def report_heatmap():
    pokemon_id = request.args.get('id')
    with db.session_scope() as session:
        return dumps(db.get_all_spawn_coords(session, pokemon_id=pokemon_id))


def main():
    args = get_args()
    app.run(debug=args.debug, threaded=True, host=args.host, port=args.port)


if __name__ == '__main__':
    main()
