#include "Preset.h"
#include <algorithm>
#include <cstdlib>
#include <cstring>
#include <dirent.h>
#include <sys/stat.h>
#include "pugixml.hpp"

namespace barchaser
{
static bool endsWith( const std::string& s, const std::string& suffix )
{
	return s.size() >= suffix.size() && s.compare( s.size() - suffix.size(), suffix.size(), suffix ) == 0;
}

std::string defaultPresetFolder()
{
	const char* home = getenv( "HOME" );
	return std::string( home ? home : "" ) + "/Documents/Resolume Arena/Presets/Advanced Output";
}

std::string newestPreset( const std::string& folder )
{
	DIR* dir = opendir( folder.c_str() );
	if( !dir )
		return "";
	std::string best;
	time_t bestTime = 0;
	while( dirent* e = readdir( dir ) )
	{
		std::string name = e->d_name;
		if( !endsWith( name, ".xml" ) || name[ 0 ] == '.' )
			continue;
		std::string path = folder + "/" + name;
		struct stat st;
		if( stat( path.c_str(), &st ) == 0 && ( best.empty() || st.st_mtime > bestTime ) )
		{
			best     = path;
			bestTime = st.st_mtime;
		}
	}
	closedir( dir );
	return best;
}

std::string resolvePreset( const std::string& text, const std::string& folder )
{
	std::string t = text;
	while( !t.empty() && ( t.back() == ' ' || t.back() == '\n' ) )
		t.pop_back();
	if( t.empty() )
		return newestPreset( folder );
	if( endsWith( t, ".xml" ) )
		return t.find( '/' ) != std::string::npos ? t : folder + "/" + t;
	return folder + "/" + t + ".xml";
}

struct Rect
{
	float l, t, r, b;
};

static bool sliceRect( const pugi::xml_node& slice, Rect& out )
{
	pugi::xml_node rect = slice.child( "InputRect" );
	if( !rect )
		return false;
	bool any = false;
	for( pugi::xml_node v = rect.child( "v" ); v; v = v.next_sibling( "v" ) )
	{
		float x = v.attribute( "x" ).as_float(), y = v.attribute( "y" ).as_float();
		if( !any )
		{
			out = { x, y, x, y };
			any = true;
		}
		else
		{
			out.l = std::min( out.l, x );
			out.t = std::min( out.t, y );
			out.r = std::max( out.r, x );
			out.b = std::max( out.b, y );
		}
	}
	return any;
}

static std::string sliceName( const pugi::xml_node& slice )
{
	for( pugi::xml_node params = slice.child( "Params" ); params; params = params.next_sibling( "Params" ) )
		for( pugi::xml_node p = params.child( "Param" ); p; p = p.next_sibling( "Param" ) )
			if( std::strcmp( p.attribute( "name" ).as_string(), "Name" ) == 0 )
				return p.attribute( "value" ).as_string();
	return "slice";
}

bool loadPreset( const std::string& path, Preset& out, std::string& error )
{
	pugi::xml_document doc;
	pugi::xml_parse_result res = doc.load_file( path.c_str() );
	if( !res )
	{
		error = std::string( "can't read " ) + path + ": " + res.description();
		return false;
	}
	Preset p;
	pugi::xml_node size = doc.select_node( "//CurrentCompositionTextureSize" ).node();
	if( size )
	{
		p.width  = size.attribute( "width" ).as_float( 1920.f );
		p.height = size.attribute( "height" ).as_float( 1080.f );
	}
	struct Screen
	{
		std::string name;
		Rect box;
		std::vector< std::pair< std::string, Rect > > slices;
	};
	std::vector< Screen > screens;
	// Any element whose tag ends with "Screen" (DmxScreen, Screen, VirtualScreen …) and has a name.
	for( pugi::xpath_node xn : doc.select_nodes( "//*[@name]" ) )
	{
		pugi::xml_node el  = xn.node();
		std::string tag    = el.name();
		if( !endsWith( tag, "Screen" ) )
			continue;
		Screen s;
		s.name = el.attribute( "name" ).as_string();
		bool any = false;
		for( pugi::xpath_node sn : el.select_nodes( ".//*" ) )
		{
			pugi::xml_node slice = sn.node();
			if( !endsWith( slice.name(), "Slice" ) )
				continue;
			Rect r;
			if( !sliceRect( slice, r ) )
				continue;
			s.slices.push_back( { sliceName( slice ), r } );
			if( !any )
			{
				s.box = r;
				any   = true;
			}
			else
			{
				s.box.l = std::min( s.box.l, r.l );
				s.box.t = std::min( s.box.t, r.t );
				s.box.r = std::max( s.box.r, r.r );
				s.box.b = std::max( s.box.b, r.b );
			}
		}
		if( any )
			screens.push_back( s );
	}
	std::stable_sort( screens.begin(), screens.end(), []( const Screen& a, const Screen& b ) {
		return a.box.l != b.box.l ? a.box.l < b.box.l : a.box.t < b.box.t;
	} );
	for( const Screen& s : screens )
		p.entries.push_back( { s.name, s.box.l, s.box.t, s.box.r, s.box.b } );
	for( const Screen& s : screens )
		if( s.slices.size() > 1 )
			for( const auto& sl : s.slices )
				p.entries.push_back( { s.name + " / " + sl.first, sl.second.l, sl.second.t, sl.second.r, sl.second.b } );
	if( p.entries.empty() )
	{
		error = "no screens with slices in " + path;
		return false;
	}
	out = p;
	return true;
}
}// namespace barchaser
