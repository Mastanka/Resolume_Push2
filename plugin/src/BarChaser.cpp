#include "BarChaser.h"
#include <cmath>
#include <cstdio>
#include <cstring>

using namespace ffglex;

enum ParamIndex : unsigned int
{
	P_PRESET = 0,
	P_RELOAD,
	P_TRACK,
	P_MASTER,
	P_EDGE,
	P_OUTSIDE,
	P_MODE,
	P_PAD0,                          // 24 pad assignments
	P_LEVEL0 = P_PAD0 + BarChaser::NPADS,// 24 live levels
	P_COUNT  = P_LEVEL0 + BarChaser::NPADS
};

static CFFGLPluginInfo PluginInfo(
	PluginFactory< BarChaser >,
	"PSB1",                                          // unique id (4 chars)
	"Bar Chaser",                                    // name
	2, 1,                                            // FFGL API 2.1
	1, 0,                                            // plugin version
	FF_EFFECT,
	"Flash this layer's picture on LED bars: one level per pad, bars from the Advanced Output preset",
	"github.com/Mastanka/Resolume_Push2" );

static const char VERTEX[] = R"(#version 410 core
uniform vec2 MaxUV;
layout( location = 0 ) in vec4 vPosition;
layout( location = 1 ) in vec2 vUV;
out vec2 uv;
void main()
{
	gl_Position = vPosition;
	uv = vUV * MaxUV;
}
)";

// One rectangle per pad in content space (0..1, y up), levels 0..1. A pixel takes the highest
// level of the pads covering it; outside every pad the Outside mode applies. Show pads paints each
// pad with its own colour and number (3x5 digits along the longer axis).
static const char FRAGMENT[] = R"(#version 410 core
uniform sampler2D InputTexture;
uniform vec2 MaxUV;
uniform vec2 Resolution;
uniform vec4 Rects[24];      // x0, y0, x1, y1 in content space
uniform float Levels[24];
uniform int NumPads;
uniform float Master;
uniform vec2 EdgeUV;
uniform int Outside;
uniform int Mode;            // 0 texture, 1 solid white, 2 show pads
in vec2 uv;
out vec4 fragColor;

const int DIGITS[10] = int[10]( 31599, 11415, 29671, 29391, 23497, 31183, 31215, 29257, 31727, 31695 );

bool digitPixel( int d, int col, int row )   // 3 columns x 5 rows, row 0 = top
{
	int bit = ( 4 - row ) * 3 + ( 2 - col );
	return ( ( DIGITS[ d ] >> bit ) & 1 ) == 1;
}

vec3 padColor( int i )
{
	float h = fract( float( i ) * 0.618034 );
	vec3 k = vec3( 0.0, 2.0, 4.0 );
	return 0.35 + 0.65 * clamp( abs( mod( h * 6.0 + k, 6.0 ) - 3.0 ) - 1.0, 0.0, 1.0 );
}

// number n (1..24) drawn inside rect r at pixel p; returns 1 on a digit pixel
float numberMask( int n, vec4 r, vec2 p )
{
	vec2 sizePx = ( r.zw - r.xy ) * Resolution;
	int nd = n >= 10 ? 2 : 1;
	bool vertical = sizePx.y > sizePx.x;
	float u = vertical ? min( sizePx.x / 3.6, sizePx.y / ( 5.6 * float( nd ) ) )
	                   : min( sizePx.y / 5.6, sizePx.x / ( 3.6 * float( nd ) ) );
	if( u < 1.0 ) return 0.0;
	vec2 local = ( p - r.xy ) * Resolution;            // px from bottom-left of the rect
	local.y = sizePx.y - local.y;                        // px from the top
	vec2 blockPx = vertical ? vec2( 3.0 * u, ( 5.0 * float( nd ) + float( nd - 1 ) ) * u )
	                        : vec2( ( 3.0 * float( nd ) + float( nd - 1 ) ) * u, 5.0 * u );
	vec2 origin = ( sizePx - blockPx ) * 0.5;
	vec2 q = local - origin;
	if( q.x < 0.0 || q.y < 0.0 || q.x >= blockPx.x || q.y >= blockPx.y ) return 0.0;
	int idx; vec2 cell;
	if( vertical ) { idx = int( q.y / ( 6.0 * u ) ); cell = vec2( q.x, q.y - float( idx ) * 6.0 * u ); }
	else           { idx = int( q.x / ( 4.0 * u ) ); cell = vec2( q.x - float( idx ) * 4.0 * u, q.y ); }
	if( cell.x >= 3.0 * u || cell.y >= 5.0 * u ) return 0.0;
	int digit = nd == 2 ? ( idx == 0 ? n / 10 : n % 10 ) : n;
	return digitPixel( digit, int( cell.x / u ), int( cell.y / u ) ) ? 1.0 : 0.0;
}

void main()
{
	vec4 color = texture( InputTexture, uv );
	vec2 p = uv / MaxUV;                                 // 0..1 content space, y up
	float factor = -1.0;
	int hit = -1;
	for( int i = 0; i < NumPads; i++ )
	{
		vec4 r = Rects[ i ];
		if( r.z <= r.x ) continue;                        // unassigned pad
		if( p.x < r.x || p.x > r.z || p.y < r.y || p.y > r.w ) continue;
		float soft = 1.0;
		if( EdgeUV.x > 0.0 )
		{
			float dx = min( p.x - r.x, r.z - p.x ) / EdgeUV.x;
			float dy = min( p.y - r.y, r.w - p.y ) / EdgeUV.y;
			soft = smoothstep( 0.0, 1.0, min( dx, dy ) );
		}
		float f = Levels[ i ] * Master * soft;
		if( f > factor ) { factor = f; hit = i; }
	}
	if( Mode == 2 )
	{
		if( hit < 0 ) { fragColor = vec4( 0.0 ); return; }
		float num = numberMask( hit + 1, Rects[ hit ], p );
		fragColor = vec4( mix( padColor( hit ) * 0.85, vec3( 0.0 ), num ), 1.0 );
		return;
	}
	if( hit < 0 )
	{
		if( Outside == 1 ) fragColor = vec4( 0.0, 0.0, 0.0, 1.0 );
		else if( Outside == 2 ) fragColor = color;
		else fragColor = vec4( 0.0 );
		return;
	}
	factor = clamp( factor, 0.0, 1.0 );
	if( Mode == 1 ) { fragColor = vec4( factor ); return; }    // solid white, premultiplied
	fragColor = color * factor;                          // premultiplied in, premultiplied out
}
)";

BarChaser::BarChaser()
{
	SetMinInputs( 1 );
	SetMaxInputs( 1 );

	SetParamInfo( P_PRESET, "Preset", FF_TYPE_TEXT, "" );
	SetParamInfof( P_RELOAD, "Reload", FF_TYPE_EVENT );
	SetOptionParamInfo( P_TRACK, "Track", 4, 0.f );
	for( unsigned int i = 0; i < 4; i++ )
		SetParamElementInfo( P_TRACK, i, std::to_string( i + 1 ).c_str(), float( i ) );
	SetParamInfo( P_MASTER, "Master", FF_TYPE_STANDARD, 1.0f );
	SetParamInfo( P_EDGE, "Edge", FF_TYPE_STANDARD, 0.0f );
	SetParamRange( P_EDGE, 0.f, 20.f );
	SetOptionParamInfo( P_OUTSIDE, "Outside", 3, 0.f );
	SetParamElementInfo( P_OUTSIDE, 0, "Transparent", 0.f );
	SetParamElementInfo( P_OUTSIDE, 1, "Black", 1.f );
	SetParamElementInfo( P_OUTSIDE, 2, "Pass through", 2.f );
	SetOptionParamInfo( P_MODE, "Mode", 3, 0.f );
	SetParamElementInfo( P_MODE, 0, "Texture", 0.f );
	SetParamElementInfo( P_MODE, 1, "Solid", 1.f );
	SetParamElementInfo( P_MODE, 2, "Show pads", 2.f );
	for( unsigned int i = P_PRESET; i <= P_TRACK; i++ )
		SetParamGroup( i, "Setup" );
	for( unsigned int i = P_MASTER; i <= P_MODE; i++ )
		SetParamGroup( i, "Look" );
	for( unsigned int k = 0; k < NPADS; k++ )
	{
		std::string name = "Pad " + std::to_string( k + 1 );
		SetOptionParamInfo( P_PAD0 + k, name.c_str(), 1, 0.f );
		SetParamElementInfo( P_PAD0 + k, 0, "\xE2\x80\x94", 0.f );// "—"
		SetParamGroup( P_PAD0 + k, "Pads" );
	}
	for( unsigned int k = 0; k < NPADS; k++ )
	{
		std::string name = "Level " + std::to_string( k + 1 );
		SetParamInfo( P_LEVEL0 + k, name.c_str(), FF_TYPE_STANDARD, 0.0f );
		SetParamGroup( P_LEVEL0 + k, "Levels" );
	}
	loadPreset( false );
	FFGLLog::LogToHost( "Bar Chaser created" );
}

BarChaser::~BarChaser()
{
}

int BarChaser::entryIndex( const std::string& name ) const
{
	for( size_t i = 0; i < preset.entries.size(); i++ )
		if( preset.entries[ i ].name == name )
			return int( i );
	return -1;
}

void BarChaser::loadPreset( bool raiseEvents )
{
	std::string folder = barchaser::defaultPresetFolder();
	presetPath         = barchaser::resolvePreset( presetText, folder );
	std::string err;
	barchaser::Preset loaded;
	if( presetPath.empty() )
	{
		presetOk = false;
		status   = "no preset in " + folder;
	}
	else if( barchaser::loadPreset( presetPath, loaded, err ) )
	{
		preset   = loaded;
		presetOk = true;
		size_t slash = presetPath.rfind( '/' );
		status = presetPath.substr( slash == std::string::npos ? 0 : slash + 1 ) + " (" + std::to_string( preset.entries.size() ) + ")";
		// First load, or a preset in which none of the assigned names exist: pad k = k-th screen.
		bool anyResolved = false;
		for( const auto& n : padName )
			anyResolved = anyResolved || ( !n.empty() && entryIndex( n ) >= 0 );
		if( !anyResolved )
			for( unsigned int k = 0; k < NPADS; k++ )
				padName[ k ] = ( k < preset.entries.size() && preset.entries[ k ].name.find( " / " ) == std::string::npos )
				                   ? preset.entries[ k ].name : "";
	}
	else
	{
		presetOk = false;
		status   = err;
	}
	FFGLLog::LogToHost( ( "Bar Chaser: " + status ).c_str() );
	refillPadElements( raiseEvents );
}

void BarChaser::refillPadElements( bool raiseEvents )
{
	std::vector< std::string > names;
	std::vector< float > values;
	names.push_back( "\xE2\x80\x94" );
	values.push_back( 0.f );
	if( presetOk )
		for( size_t i = 0; i < preset.entries.size(); i++ )
		{
			names.push_back( preset.entries[ i ].name );
			values.push_back( float( i + 1 ) );
		}
	for( unsigned int k = 0; k < NPADS; k++ )
	{
		int idx       = presetOk ? entryIndex( padName[ k ] ) : -1;
		padValue[ k ] = idx < 0 ? 0.f : float( idx + 1 );
		if( idx < 0 )
			padName[ k ].clear();
		if( !raiseEvents )
		{
			// Hosts set every parameter to its declared default right after creating the instance,
			// so the default must be the assignment itself or it would be wiped to "—".
			if( ParamInfo* info = FindParamInfo( P_PAD0 + k ) )
				info->defaultFloatVal = padValue[ k ];
		}
		SetParamElements( P_PAD0 + k, names, values, raiseEvents );
		if( raiseEvents )
			RaiseParamEvent( P_PAD0 + k, FF_EVENT_FLAG_VALUE );
	}
}

FFResult BarChaser::InitGL( const FFGLViewportStruct* vp )
{
	if( !shader.Compile( VERTEX, FRAGMENT ) )
	{
		FFGLLog::LogToHost( "Bar Chaser: shader failed to compile" );
		DeInitGL();
		return FF_FAIL;
	}
	if( !quad.Initialise() )
	{
		DeInitGL();
		return FF_FAIL;
	}
	const char* ver = (const char*)glGetString( GL_VERSION );
	FFGLLog::LogToHost( ( std::string( "Bar Chaser: InitGL viewport " ) + std::to_string( vp->width ) + "x" + std::to_string( vp->height )
	                      + " GL " + ( ver ? ver : "?" ) ).c_str() );
	diagFrames = 0;
	return CFFGLPlugin::InitGL( vp );
}

// One-time look at what the host hands us, written to Resolume's log (~/Library/Logs/Resolume Arena/).
static void logInput( const FFGLTextureStruct& t, GLuint hostFBO, int frame )
{
	GLint w = 0, h = 0, fmt = 0, minf = 0, magf = 0, sampler = 0, fbo = 0, vp[ 4 ] = { 0, 0, 0, 0 };
	glGetTexLevelParameteriv( GL_TEXTURE_2D, 0, GL_TEXTURE_WIDTH, &w );
	glGetTexLevelParameteriv( GL_TEXTURE_2D, 0, GL_TEXTURE_HEIGHT, &h );
	glGetTexLevelParameteriv( GL_TEXTURE_2D, 0, GL_TEXTURE_INTERNAL_FORMAT, &fmt );
	glGetTexParameteriv( GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, &minf );
	glGetTexParameteriv( GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, &magf );
	glGetIntegerv( GL_SAMPLER_BINDING, &sampler );
	glGetIntegerv( GL_FRAMEBUFFER_BINDING, &fbo );
	glGetIntegerv( GL_VIEWPORT, vp );
	char buf[ 400 ];
	std::snprintf( buf, sizeof buf,
	               "Bar Chaser: frame %d input handle %u isTexture %d size %ux%u hw %ux%u level0 %dx%d fmt 0x%x min 0x%x mag 0x%x sampler %d fbo %d hostFBO %u viewport %d,%d %dx%d err 0x%x",
	               frame, t.Handle, (int)glIsTexture( t.Handle ), t.Width, t.Height, t.HardwareWidth, t.HardwareHeight, w, h, fmt, minf, magf,
	               sampler, fbo, hostFBO, vp[ 0 ], vp[ 1 ], vp[ 2 ], vp[ 3 ], glGetError() );
	FFGLLog::LogToHost( buf );
}

FFResult BarChaser::ProcessOpenGL( ProcessOpenGLStruct* pGL )
{
	if( pGL->numInputTextures < 1 || pGL->inputTextures[ 0 ] == nullptr )
		return FF_FAIL;
	ScopedShaderBinding shaderBinding( shader.GetGLID() );
	ScopedSamplerActivation activateSampler( 0 );
	Scoped2DTextureBinding textureBinding( pGL->inputTextures[ 0 ]->Handle );
	// A sampler object left on unit 0 or a mipmap filter on a texture without mipmaps would make
	// every sample black: sample with plain linear filtering and restore the host's state after.
	GLint prevSampler = 0, prevMin = 0, prevMag = 0;
	glGetIntegerv( GL_SAMPLER_BINDING, &prevSampler );
	glBindSampler( 0, 0 );
	glGetTexParameteriv( GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, &prevMin );
	glGetTexParameteriv( GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, &prevMag );
	glTexParameteri( GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR );
	glTexParameteri( GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR );
	if( diagFrames < 3 )
		logInput( *pGL->inputTextures[ 0 ], pGL->HostFBO, diagFrames++ );
	shader.Set( "InputTexture", 0 );
	FFGLTexCoords maxCoords = GetMaxGLTexCoords( *pGL->inputTextures[ 0 ] );
	shader.Set( "MaxUV", maxCoords.s, maxCoords.t );
	float w = float( pGL->inputTextures[ 0 ]->Width ), h = float( pGL->inputTextures[ 0 ]->Height );
	shader.Set( "Resolution", w, h );

	float rects[ NPADS * 4 ];
	for( unsigned int k = 0; k < NPADS; k++ )
	{
		int idx = presetOk && !padName[ k ].empty() ? entryIndex( padName[ k ] ) : -1;
		if( idx < 0 )
		{
			rects[ k * 4 ] = rects[ k * 4 + 1 ] = rects[ k * 4 + 2 ] = rects[ k * 4 + 3 ] = 0.f;
			continue;
		}
		const barchaser::Slice& s = preset.entries[ idx ];
		rects[ k * 4 + 0 ] = s.left / preset.width;
		rects[ k * 4 + 1 ] = 1.f - s.bottom / preset.height;// y up in content space
		rects[ k * 4 + 2 ] = s.right / preset.width;
		rects[ k * 4 + 3 ] = 1.f - s.top / preset.height;
	}
	glUniform4fv( shader.FindUniform( "Rects" ), NPADS, rects );
	glUniform1fv( shader.FindUniform( "Levels" ), NPADS, level.data() );
	glUniform1i( shader.FindUniform( "NumPads" ), int( NPADS ) );
	glUniform1f( shader.FindUniform( "Master" ), master );
	glUniform2f( shader.FindUniform( "EdgeUV" ), edge / preset.width, edge / preset.height );
	glUniform1i( shader.FindUniform( "Outside" ), int( outside + 0.5f ) );
	glUniform1i( shader.FindUniform( "Mode" ), int( mode + 0.5f ) );
	quad.Draw();
	glTexParameteri( GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, prevMin );
	glTexParameteri( GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, prevMag );
	glBindSampler( 0, (GLuint)prevSampler );
	return FF_SUCCESS;
}

FFResult BarChaser::DeInitGL()
{
	shader.FreeGLResources();
	quad.Release();
	return FF_SUCCESS;
}

FFResult BarChaser::SetFloatParameter( unsigned int index, float value )
{
	if( index >= P_LEVEL0 && index < P_COUNT )
	{
		level[ index - P_LEVEL0 ] = value < 0.f ? 0.f : ( value > 1.f ? 1.f : value );
		return FF_SUCCESS;
	}
	if( index >= P_PAD0 && index < P_LEVEL0 )
	{
		unsigned int k = index - P_PAD0;
		int idx        = int( value + 0.5f ) - 1;// element value = entry index + 1, 0 = "—"
		padValue[ k ]  = value;
		padName[ k ]   = ( presetOk && idx >= 0 && idx < int( preset.entries.size() ) ) ? preset.entries[ idx ].name : "";
		return FF_SUCCESS;
	}
	switch( index )
	{
	case P_RELOAD:
		if( value != 0.f )
			loadPreset( true );
		return FF_SUCCESS;
	case P_TRACK:
		track = value;
		return FF_SUCCESS;
	case P_MASTER:
		master = value;
		return FF_SUCCESS;
	case P_EDGE:
		edge = value;
		return FF_SUCCESS;
	case P_OUTSIDE:
		outside = value;
		return FF_SUCCESS;
	case P_MODE:
		mode = value;
		return FF_SUCCESS;
	}
	return FF_FAIL;
}

float BarChaser::GetFloatParameter( unsigned int index )
{
	if( index >= P_LEVEL0 && index < P_COUNT )
		return level[ index - P_LEVEL0 ];
	if( index >= P_PAD0 && index < P_LEVEL0 )
		return padValue[ index - P_PAD0 ];
	switch( index )
	{
	case P_TRACK:
		return track;
	case P_MASTER:
		return master;
	case P_EDGE:
		return edge;
	case P_OUTSIDE:
		return outside;
	case P_MODE:
		return mode;
	}
	return 0.f;
}

FFResult BarChaser::SetTextParameter( unsigned int index, const char* value )
{
	if( index != P_PRESET )
		return FF_FAIL;
	std::string text = value ? value : "";
	if( text != presetText )
	{
		presetText = text;
		loadPreset( true );
	}
	return FF_SUCCESS;
}

char* BarChaser::GetTextParameter( unsigned int index )
{
	if( index != P_PRESET )
		return nullptr;
	return const_cast< char* >( presetText.c_str() );
}

char* BarChaser::GetParameterDisplay( unsigned int index )
{
	if( index == P_PRESET )
		displayBuffer = status;
	else if( index == P_EDGE )
		displayBuffer = std::to_string( int( edge + 0.5f ) ) + " px";
	else if( index >= P_LEVEL0 && index < P_COUNT )
		displayBuffer = std::to_string( int( level[ index - P_LEVEL0 ] * 100.f + 0.5f ) ) + " %";
	else if( index == P_MASTER )
		displayBuffer = std::to_string( int( master * 100.f + 0.5f ) ) + " %";
	else
		return CFFGLPlugin::GetParameterDisplay( index );
	return const_cast< char* >( displayBuffer.c_str() );
}
