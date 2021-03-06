from django.contrib.gis.geoip import GeoIPException
from django.shortcuts import render_to_response
from django.template import RequestContext
from django.template import loader
from django.template import Context
from django.http import HttpResponseRedirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.core import serializers
from django.core.urlresolvers import reverse
from django_countries import countries

from api.processors import get_event_by_id
from api.processors import get_filtered_events
from api.processors import get_approved_events
from api.processors import get_pending_events
from api.processors import get_created_events
from web.forms.event_form import AddEventForm
from web.forms.event_form import SearchEventForm
from web.processors.event import get_initial_data
from web.processors.event import change_event_status
from web.processors.event import reject_event_status
from web.processors.event import create_or_update_event
from web.processors.user import update_user_email
from web.processors.event import get_client_ip
from web.processors.event import get_lat_lon_from_user_ip
from web.processors.event import list_countries
from web.processors.event import get_country
from web.processors.media import process_image
from web.processors.media import ImageSizeTooLargeException
from web.processors.media import UploadImageError
from web.decorators.events import can_edit_event
from web.decorators.events import can_moderate_event
from web.decorators.events import is_ambassador


"""
Do not Query the database directly from te view.
Use a processors file within the api app, put all of your queries there and
then call your newly created function in view!!! .-Erika
"""


def index(request, country_code=None):
	template = 'pages/index.html'
	events = get_approved_events()
	map_events = serializers.serialize('json', events, fields=('geoposition', 'title', 'pk', 'slug', 'description', 'picture'))
	user_ip = get_client_ip(forwarded=request.META.get('HTTP_X_FORWARDED_FOR'),
	                        remote=request.META.get('REMOTE_ADDR'))

	country = get_country(country_code, user_ip)

	try:
		lan_lon = get_lat_lon_from_user_ip(user_ip)
	except GeoIPException:
		lan_lon = (58.08695, 5.58121)

	events = get_approved_events(order='pub_date', country_code=country.get('country_code', None))

	all_countries = list_countries()
	return render_to_response(
		template, {
			'latest_events': events,
			'map_events': map_events,
			'lan_lon': lan_lon,
			'country': country,
			'all_countries': all_countries,
		},
		context_instance=RequestContext(request))


@login_required
def add_event(request):
	event_form = AddEventForm(initial={'user_email': request.user.email})
	user = request.user

	if request.method == 'POST':
		event_form = AddEventForm(data=request.POST, files=request.FILES)
	if event_form.is_valid():
		picture = request.FILES.get('picture', None)
		event_data = {}
		try:
			if picture:
				if picture.size > (256 * 1024):
					raise ImageSizeTooLargeException('Image size too large.')

				event_data['picture'] = process_image(picture)

			event_data.update(event_form.cleaned_data)
			event_data['creator'] = user

			# checking if user entered a different email than in her profile
			if user.email != event_data['user_email']:
				update_user_email(user.id, event_data['user_email'])
			event_data.pop('user_email')

			event = create_or_update_event(**event_data)

			t = loader.get_template('alerts/thank_you.html')
			c = Context({'event': event, })
			messages.info(request, t.render(c))

			return HttpResponseRedirect(reverse('web.view_event', args=[event.pk, event.slug]))

		except ImageSizeTooLargeException:
			messages.error(request, 'The image is just a bit too big for us. '
			                        'Please reduce your image size and try agin.')
		except UploadImageError as e:
			messages.error(request, e.message)

	return render_to_response("pages/add_event.html", {
		'form': event_form,
	}, context_instance=RequestContext(request))


@login_required
@can_edit_event
def edit_event(request, event_id):
	event = get_event_by_id(event_id)
	user = request.user
	initial = get_initial_data(event)
	initial['user_email'] = request.user.email

	event_data = {}

	if request.method == 'POST':
		event_form = AddEventForm(data=request.POST, files=request.FILES)
	else:
		event_form = AddEventForm(initial=initial)


	if event_form.is_valid():
		picture = request.FILES.get('picture', None)
		event_data = event_form.cleaned_data

		event_data['creator'] = request.user

		# checking if user entered a different email than in her profile
		if user.email != event_data['user_email']:
			update_user_email(user.id, event_data['user_email'])
		event_data.pop('user_email')

		try:
			if picture:
				if picture.size > (256 * 1024):
					raise ImageSizeTooLargeException('Image size too large.')

				event_data['picture'] = process_image(picture)
			else:
				del event_data['picture']

			create_or_update_event(event_id, **event_data)

			return HttpResponseRedirect(reverse('web.view_event',
			                                    kwargs={'event_id': event.id, 'slug': event.slug}))

		except ImageSizeTooLargeException:
			messages.error(request, 'The image is just a bit too big for us (must be up to 256 kb). '
			                        'Please reduce your image size and try agin.')
		except UploadImageError as e:
			messages.error(request, e.message)
		
	return render_to_response(
		'pages/add_event.html', {
			'form': event_form,
			'address': event_data.get('location', None),
			'editing': True,
			'picture_url': event.picture,
		}, context_instance=RequestContext(request))


def view_event_by_country(request, country_code):
	event_list = get_approved_events(country_code=country_code)

	return render_to_response(
		'pages/list_events.html', {
			'event_list': event_list,
		}, context_instance=RequestContext(request))


def view_event(request, event_id, slug):
	event = get_event_by_id(event_id)

	return render_to_response(
		'pages/view_event.html', {
			'event': event,
		}, context_instance=RequestContext(request))


@login_required
@is_ambassador
def list_pending_events(request, country_code):
	"""
	Display a list of pending events.
	"""

	active_page = request.GET.get('page','')

	if request.user.is_staff:
		event_list = get_pending_events(past=True)
		event_list = sorted(event_list, key=lambda a: a.country.code)
	else:
		event_list = get_pending_events(country_code=country_code, past=True)

	country_name = unicode(dict(countries)[country_code])

	return render_to_response(
		'pages/list_events.html', {
			'event_list': event_list,
			'status': 'pending',
			'country_code': country_code,
			'country_name': country_name,
			'active_page': active_page
		}, context_instance=RequestContext(request))


@login_required
@is_ambassador
def list_approved_events(request, country_code):
	"""
	Display a list of approved events.
	"""

	event_list = get_approved_events(country_code=country_code, past=True)

	country_name = unicode(dict(countries)[country_code])

	return render_to_response('pages/list_events.html', {
		'event_list': event_list,
		'status': 'approved',
		'country_code': country_code,
		'country_name': country_name
	}, context_instance=RequestContext(request))


@login_required
def created_events(request):
	"""
	Display a list of pending events.
	"""
	creator = request.user
	event_list = get_created_events(creator=creator, past=True)

	return render_to_response(
		'pages/list_user_events.html', {
			'event_list': event_list,
		}, context_instance=RequestContext(request))


def search_events(request):
		user_ip = get_client_ip(forwarded=request.META.get('HTTP_X_FORWARDED_FOR'),
		                        remote=request.META.get('REMOTE_ADDR'))
		country_code = request.GET.get('country_code', None)
		country = get_country(country_code, user_ip)
		events = get_approved_events(country_code=country)

		if request.method == 'POST':
			form = SearchEventForm(request.POST)

			if form.is_valid():
				search_filter = form.cleaned_data.get('search', None)
				country_filter = form.cleaned_data.get('country', None)
				theme_filter = form.cleaned_data.get('theme', None)
				audience_filter = form.cleaned_data.get('audience', None)

				events = get_filtered_events(search_filter, country_filter, theme_filter, audience_filter)
				country = {'country_code': country_filter}
		else:
			form = SearchEventForm(country_code=country['country_code'])
			events = get_approved_events(country_code=country['country_code'])

		return render_to_response(
			'pages/search_events.html', {
				'events': events,
				'form': form,
			    'country': country['country_code'],
			}, context_instance=RequestContext(request))


@login_required
@can_moderate_event
def change_status(request, event_id):
	event = change_event_status(event_id)

	return HttpResponseRedirect(reverse('web.view_event', args=[event_id, event.slug]))

@login_required
@can_moderate_event
def reject_status(request, event_id):
	event = reject_event_status(event_id)

	return HttpResponseRedirect(reverse('web.view_event', args=[event_id, event.slug]))


