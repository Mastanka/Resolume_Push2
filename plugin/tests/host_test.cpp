// Minimal FFGL host: loads "Bar Chaser.bundle" in an offscreen OpenGL 4.1 context, feeds it a
// test picture (left half red, right half green), renders one frame and checks the pixels.
//   host_test <bundle>/Contents/MacOS/Bar\ Chaser  <preset_small.xml>
#include <OpenGL/OpenGL.h>
#include <OpenGL/gl3.h>
#include <dlfcn.h>
#include <cstdio>
#include <cstring>
#include <string>
#include <vector>
#include "ffgl/FFGL.h"

typedef FFMixed ( *MainFn )( FFUInt32, FFMixed, FFInstanceID );

static const int W = 128, H = 72;

#define CHECK( cond, msg )                                                    \
	do                                                                        \
	{                                                                         \
		if( !( cond ) )                                                       \
		{                                                                     \
			std::fprintf( stderr, "FAIL line %d: %s\n", __LINE__, msg );      \
			return 1;                                                         \
		}                                                                     \
	} while( 0 )

struct Pixel
{
	unsigned char r, g, b, a;
};

static Pixel px( const std::vector< unsigned char >& buf, int x, int y )
{
	size_t i = ( size_t( y ) * W + x ) * 4;
	return { buf[ i ], buf[ i + 1 ], buf[ i + 2 ], buf[ i + 3 ] };
}

int main( int argc, char** argv )
{
	if( argc < 3 )
	{
		std::fprintf( stderr, "usage: host_test <plugin executable> <preset.xml>\n" );
		return 2;
	}
	// ---- offscreen GL 4 core context
	CGLPixelFormatAttribute attrs[] = { kCGLPFAOpenGLProfile, (CGLPixelFormatAttribute)kCGLOGLPVersion_GL4_Core,
	                                    kCGLPFAAccelerated, (CGLPixelFormatAttribute)0 };
	CGLPixelFormatObj pf = nullptr;
	GLint npf            = 0;
	CHECK( CGLChoosePixelFormat( attrs, &pf, &npf ) == kCGLNoError && pf, "no pixel format" );
	CGLContextObj ctx = nullptr;
	CHECK( CGLCreateContext( pf, nullptr, &ctx ) == kCGLNoError && ctx, "no GL context" );
	CGLSetCurrentContext( ctx );
	std::printf( "GL %s\n", (const char*)glGetString( GL_VERSION ) );

	// ---- input texture: left half red, right half green, opaque
	std::vector< unsigned char > in( W * H * 4 );
	for( int y = 0; y < H; y++ )
		for( int x = 0; x < W; x++ )
		{
			unsigned char* p = &in[ ( size_t( y ) * W + x ) * 4 ];
			p[ 0 ] = x < W / 2 ? 255 : 0;
			p[ 1 ] = x < W / 2 ? 0 : 255;
			p[ 2 ] = 0;
			p[ 3 ] = 255;
		}
	GLuint tex = 0;
	glGenTextures( 1, &tex );
	glBindTexture( GL_TEXTURE_2D, tex );
	glTexImage2D( GL_TEXTURE_2D, 0, GL_RGBA8, W, H, 0, GL_RGBA, GL_UNSIGNED_BYTE, in.data() );
	glTexParameteri( GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR );
	glTexParameteri( GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR );
	glBindTexture( GL_TEXTURE_2D, 0 );

	// ---- output FBO
	GLuint outTex = 0, fbo = 0;
	glGenTextures( 1, &outTex );
	glBindTexture( GL_TEXTURE_2D, outTex );
	glTexImage2D( GL_TEXTURE_2D, 0, GL_RGBA8, W, H, 0, GL_RGBA, GL_UNSIGNED_BYTE, nullptr );
	glBindTexture( GL_TEXTURE_2D, 0 );
	glGenFramebuffers( 1, &fbo );
	glBindFramebuffer( GL_FRAMEBUFFER, fbo );
	glFramebufferTexture2D( GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_TEXTURE_2D, outTex, 0 );
	CHECK( glCheckFramebufferStatus( GL_FRAMEBUFFER ) == GL_FRAMEBUFFER_COMPLETE, "fbo incomplete" );
	glViewport( 0, 0, W, H );

	// ---- load the plugin
	void* lib = dlopen( argv[ 1 ], RTLD_NOW );
	CHECK( lib, dlerror() );
	MainFn plugMain = (MainFn)dlsym( lib, "plugMain" );
	CHECK( plugMain, "no plugMain" );
	FFMixed arg;
	arg.UIntValue = 0;
	CHECK( plugMain( FF_INITIALISE_V2, arg, nullptr ).UIntValue == FF_SUCCESS, "initialise" );
	PluginInfoStruct* info = (PluginInfoStruct*)plugMain( FF_GET_INFO, arg, nullptr ).PointerValue;
	CHECK( info && std::strncmp( (const char*)info->PluginName, "Bar Chaser", 10 ) == 0, "plugin name" );
	unsigned int nparams = plugMain( FF_GET_NUM_PARAMETERS, arg, nullptr ).UIntValue;
	CHECK( nparams == 55, "55 parameters expected" );
	FFGLViewportStruct vp = { 0, 0, (GLuint)W, (GLuint)H };
	arg.PointerValue      = &vp;
	FFInstanceID inst     = plugMain( FF_INSTANTIATE_GL, arg, nullptr ).PointerValue;
	CHECK( inst, "instantiate failed (shader?)" );

	auto setFloat = [&]( unsigned int index, float v ) {
		SetParameterStruct s;
		s.ParameterNumber = index;
		std::memcpy( &s.NewParameterValue.UIntValue, &v, sizeof( float ) );
		FFMixed a;
		a.PointerValue = &s;
		return plugMain( FF_SET_PARAMETER, a, inst ).UIntValue;
	};
	auto setText = [&]( unsigned int index, const char* t ) {
		SetParameterStruct s;
		s.ParameterNumber              = index;
		s.NewParameterValue.PointerValue = (void*)t;
		FFMixed a;
		a.PointerValue = &s;
		return plugMain( FF_SET_PARAMETER, a, inst ).UIntValue;
	};
	CHECK( setText( 0, argv[ 2 ] ) == FF_SUCCESS, "set preset" );  // Pad 1 = Bar A (x 145-175 of 1920, y 72-530 of 1080)

	FFGLTextureStruct texInfo  = { (FFUInt32)W, (FFUInt32)H, (FFUInt32)W, (FFUInt32)H, tex };
	FFGLTextureStruct* texPtrs[ 1 ] = { &texInfo };
	ProcessOpenGLStruct pgl;
	pgl.numInputTextures = 1;
	pgl.inputTextures    = texPtrs;
	pgl.HostFBO          = fbo;
	std::vector< unsigned char > out( W * H * 4 );
	auto render = [&]() {
		glBindFramebuffer( GL_FRAMEBUFFER, fbo );
		glViewport( 0, 0, W, H );
		glClearColor( 0, 0, 0, 0 );
		glClear( GL_COLOR_BUFFER_BIT );
		FFMixed a;
		a.PointerValue = &pgl;
		FFUInt32 r     = plugMain( FF_PROCESS_OPENGL, a, inst ).UIntValue;
		glFinish();
		glBindFramebuffer( GL_FRAMEBUFFER, fbo );
		glReadPixels( 0, 0, W, H, GL_RGBA, GL_UNSIGNED_BYTE, out.data() );
		return r;
	};

	// 1. Texture mode, Outside = Pass through (2): output == input everywhere
	CHECK( setFloat( 5, 2.0f ) == FF_SUCCESS, "set outside" );
	CHECK( render() == FF_SUCCESS, "render 1" );
	Pixel L = px( out, 8, H / 2 ), R = px( out, W - 8, H / 2 );
	std::printf( "pass-through: left=(%d,%d,%d,%d) right=(%d,%d,%d,%d)\n", L.r, L.g, L.b, L.a, R.r, R.g, R.b, R.a );
	CHECK( L.r > 200 && L.g < 50 && L.a > 200, "pass through: left half must be red" );
	CHECK( R.g > 200 && R.r < 50 && R.a > 200, "pass through: right half must be green" );

	// 2. Texture mode, Outside = Transparent, Level 1 = 1: only Bar A's rectangle shows the input
	CHECK( setFloat( 5, 0.0f ) == FF_SUCCESS, "set outside 0" );
	CHECK( setFloat( 31, 1.0f ) == FF_SUCCESS, "set level 1" );
	CHECK( render() == FF_SUCCESS, "render 2" );
	int bx = int( 160.0 / 1920.0 * W ), by = int( ( 1.0 - 300.0 / 1080.0 ) * H );// inside Bar A (y up)
	Pixel inBar = px( out, bx, by ), outside = px( out, W - 8, H / 2 );
	std::printf( "level 1: in bar=(%d,%d,%d,%d) outside=(%d,%d,%d,%d)\n", inBar.r, inBar.g, inBar.b, inBar.a, outside.r, outside.g, outside.b, outside.a );
	CHECK( inBar.r > 200 && inBar.a > 200, "level 1: bar A must show the (red) input" );
	CHECK( outside.a == 0, "level 1: outside must be transparent" );

	// 3. Level 0.5 halves it; Solid mode gives white
	CHECK( setFloat( 31, 0.5f ) == FF_SUCCESS, "set level 0.5" );
	CHECK( render() == FF_SUCCESS, "render 3" );
	inBar = px( out, bx, by );
	CHECK( inBar.r > 100 && inBar.r < 160 && inBar.a > 100 && inBar.a < 160, "level 0.5 must halve rgb and alpha" );
	CHECK( setFloat( 6, 1.0f ) == FF_SUCCESS, "set mode solid" );
	CHECK( setFloat( 31, 1.0f ) == FF_SUCCESS, "set level 1" );
	CHECK( render() == FF_SUCCESS, "render 4" );
	inBar = px( out, bx, by );
	CHECK( inBar.r > 200 && inBar.g > 200 && inBar.b > 200 && inBar.a > 200, "solid mode must be white" );

	// 4. Show pads paints bar A with an opaque colour
	CHECK( setFloat( 6, 2.0f ) == FF_SUCCESS, "set mode show pads" );
	CHECK( render() == FF_SUCCESS, "render 5" );
	inBar = px( out, bx, by );
	CHECK( inBar.a > 200 && ( inBar.r + inBar.g + inBar.b ) > 60, "show pads must paint the bar" );

	arg.UIntValue = 0;
	plugMain( FF_DEINSTANTIATE_GL, arg, inst );
	plugMain( FF_DEINITIALISE, arg, nullptr );
	std::printf( "OK\n" );
	return 0;
}
