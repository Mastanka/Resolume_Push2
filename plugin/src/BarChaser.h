// Bar Chaser — FFGL effect: masks the layer's picture to LED bars (from the Advanced Output preset)
// with one live level per pad. The Push bridge drives "Level 1".."Level 24" through Resolume's API.
#pragma once
#include <array>
#include <string>
#include <vector>
#include <FFGLSDK.h>
#include "Preset.h"

class BarChaser : public CFFGLPlugin
{
public:
	static const unsigned int NPADS = 24;

	BarChaser();
	~BarChaser() override;

	FFResult InitGL( const FFGLViewportStruct* vp ) override;
	FFResult ProcessOpenGL( ProcessOpenGLStruct* pGL ) override;
	FFResult DeInitGL() override;

	FFResult SetFloatParameter( unsigned int index, float value ) override;
	float GetFloatParameter( unsigned int index ) override;
	FFResult SetTextParameter( unsigned int index, const char* value ) override;
	char* GetTextParameter( unsigned int index ) override;
	char* GetParameterDisplay( unsigned int index ) override;

private:
	void loadPreset( bool raiseEvents );
	void refillPadElements( bool raiseEvents );
	int entryIndex( const std::string& name ) const;

	ffglex::FFGLShader shader;
	ffglex::FFGLScreenQuad quad;

	std::string presetText;         // the Preset parameter (what the user typed)
	std::string presetPath;         // resolved file
	std::string status;             // shown in the Preset parameter's display text
	barchaser::Preset preset;
	bool presetOk = false;

	float track   = 0.f;            // option value 0..3 → track 1..4
	float master  = 1.f;
	float edge    = 0.f;            // px
	float outside = 0.f;            // 0 transparent, 1 black, 2 pass through
	float mode    = 0.f;            // 0 texture, 1 solid, 2 show pads
	std::array< std::string, NPADS > padName;   // "" = unassigned ("—")
	std::array< float, NPADS > padValue{};      // option value = element index (0 = "—")
	std::array< float, NPADS > level{};
	std::string displayBuffer;
};
