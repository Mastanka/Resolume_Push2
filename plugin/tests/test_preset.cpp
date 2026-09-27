// Unit test for the preset parser.  Run by ctest with tests/fixtures/preset_small.xml.
#include <cstdio>
#include <cstdlib>
#include "Preset.h"

#define CHECK( cond )                                                             \
	do                                                                            \
	{                                                                             \
		if( !( cond ) )                                                           \
		{                                                                         \
			std::fprintf( stderr, "FAIL line %d: %s\n", __LINE__, #cond );        \
			return 1;                                                             \
		}                                                                         \
	} while( 0 )

int main( int argc, char** argv )
{
	using namespace barchaser;
	if( argc < 2 )
	{
		std::fprintf( stderr, "usage: test_preset <preset_small.xml>\n" );
		return 2;
	}
	std::string path = argv[ 1 ];
	Preset p;
	std::string err;
	CHECK( loadPreset( path, p, err ) );
	CHECK( p.width == 1920.f && p.height == 1080.f );
	// fixtures in the file's (= Arena's list) order, then the whole screens in the same order
	CHECK( p.entries.size() == 9 && p.fixtures == 5 );
	CHECK( p.entries[ 0 ].name == "Bar B / 1 - 423 141 RGB" );
	CHECK( p.entries[ 1 ].name == "Bar A / 1 - 423 141 RGB" );
	CHECK( p.entries[ 2 ].name == "Bar A copy / 1 - 423 141 RGB" );
	CHECK( p.entries[ 3 ].name == "Bar C / 1 - 855 h3 2m grb" );
	CHECK( p.entries[ 4 ].name == "Bar C / second slice" );
	CHECK( p.entries[ 5 ].name == "Bar B" );
	CHECK( p.entries[ 6 ].name == "Bar A" );
	CHECK( p.entries[ 7 ].name == "Bar A copy" );
	CHECK( p.entries[ 8 ].name == "Bar C" );
	const Slice& a = p.entries[ 1 ];
	CHECK( (int)( a.left + 0.5f ) == 145 && (int)( a.top + 0.5f ) == 72 && (int)( a.right + 0.5f ) == 175 && (int)( a.bottom + 0.5f ) == 530 );
	const Slice& b = p.entries[ 0 ];
	CHECK( (int)b.left == 345 && (int)b.top == 70 && (int)b.right == 375 && (int)b.bottom == 532 );
	const Slice& c = p.entries[ 8 ];
	CHECK( (int)c.left == 545 && (int)c.top == 70 && (int)c.right == 600 && (int)c.bottom == 1009 );
	const Slice& c2 = p.entries[ 4 ];
	CHECK( (int)c2.left == 580 && (int)c2.right == 600 && (int)c2.bottom == 200 );

	Preset none;
	CHECK( !loadPreset( path + ".missing", none, err ) && !err.empty() );

	std::string folder = path.substr( 0, path.rfind( '/' ) );
	CHECK( newestPreset( folder ) == path );
	CHECK( newestPreset( folder + "/nope" ).empty() );
	CHECK( resolvePreset( "", folder ) == path );
	CHECK( resolvePreset( "preset_small", folder ) == path );
	CHECK( resolvePreset( path, folder ) == path );
	std::printf( "OK\n" );
	return 0;
}
